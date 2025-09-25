import requests
from fastapi import HTTPException, status
from jose import jwt
from app.config.settings import keycloak_openid, get_settings
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Union, List
import os

from app.config.logger_config import logger


class AuthService:
    """
    Service for authentication and authorization operations
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.keycloak_client_id = self.settings.keycloak_client_id

        raw_key = keycloak_openid.public_key()

        if not raw_key.startswith("-----BEGIN"):
            # Format the public key proparly for PEM format
            self.public_key = (
                f"-----BEGIN PUBLIC KEY-----\n{raw_key}\n-----END PUBLIC KEY-----"
            )
        else:
            self.public_key = raw_key

        self._admin_token_cache: Optional[dict] = None

        # SSL verification for requests
        self.ssl_verify = self._get_ssl_verify()

    def _get_ssl_verify(self) -> Union[str, bool]:
        """
        Determine SSL verification method based on certificate availability
        """
        # Check both possible certificate paths
        cert_paths = ["/app/certs/keycloak.pem", "certs/keycloak.pem"]

        for cert_path in cert_paths:
            if os.path.exists(cert_path):
                logger.info(f"Using SSL certificate: {cert_path}")
                return cert_path

        logger.warning("SSL certificate not found, disabling SSL verification")
        return False

    def validate_token(self, token: str) -> Dict[str, Any]:
        """
        Validate and decode the JWT token

        Args:
            token: The JWT token to validate

        Returns:
            dict: The decoded token payload

        Raises:
            HTTPException: If the token is invalid
        """
        try:
            # Log token validation attempt (first 10 chars only for security)
            logger.info(f"Validating token starting with: {token[:10]}...")

            # Try to decode with python-jose library
            try:
                payload = jwt.decode(
                    token,
                    self.public_key,
                    algorithms=["RS256"],
                    options={
                        "verify_aud": False,  # Disable audience verification completely
                        "verify_exp": True,  # Keep expiration verification
                        "verify_iat": True,  # Verify issued at
                        "verify_nbf": True,  # Verify not before
                    },
                )

                # Verify the token has a username
                username = payload.get("preferred_username")
                if not username:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid token: missing username",
                        headers={"WWW-Authenticate": "Bearer"},
                    )

                logger.info(f"Token validated successfully for user: {username}")
                return payload

            except Exception as e:
                logger.error(f"Jose JWT decode error: {str(e)}")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"Invalid token: {str(e)}",
                    headers={"WWW-Authenticate": "Bearer"},
                )

        except HTTPException:
            # Re-raise HTTP exceptions
            raise

        except Exception as e:
            # Catch-all for any other exceptions
            logger.error(f"Unexpected token validation error: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

    def exchange_token(
        self, code: str, redirect_uri: str, code_verifier: str
    ) -> Dict[str, Any]:
        """
        Exchange authorization code for tokens
        """
        try:
            token = keycloak_openid.token(
                grant_type="authorization_code",
                code=code,
                redirect_uri=redirect_uri,
                code_verifier=code_verifier,  # type: ignore
                scope="openid profile email account",
            )
            return token
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to exchange token: {str(e)}",
                headers={"WWW-Authenticate": "Bearer"},
            )

    def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        """
        Refresh the access token using the refresh token

        Args:
            refresh_token: The refresh token to use

        Returns:
            dict: New tokens including access_token, refresh_token and user_info

        Raises:
            HTTPException: If the refresh token is invalid or expired
        """
        try:
            logger.info(
                f"Attempting to refresh token starting with: {refresh_token[:10]}..."
            )

            token_response = keycloak_openid.refresh_token(refresh_token)

            # Get user info with the new token
            user_info = self.get_user_info(token_response["access_token"])

            return {
                "access_token": token_response["access_token"],
                "expires_in": token_response.get("expires_in", 300),
                "refresh_token": token_response["refresh_token"],
                "token_type": "Bearer",
                "user_info": user_info,
            }
        except Exception as e:
            logger.error(f"Failed to refresh token: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token invalid or expired",
                headers={"WWW-Authenticate": "Bearer"},
            )

    def get_user_info(self, access_token: str) -> Dict[str, Any]:
        """
        Get user information from Keycloak using the access token
        """
        try:
            user_info = keycloak_openid.userinfo(access_token)
            return user_info
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Failed to get user info",
                headers={"WWW-Authenticate": "Bearer"},
            )

    def _get_admin_token(self) -> str:
        """
        Get admin token from Keycloak with caching
        """
        # Check if we have a cached token that's still valid
        if (
            self._admin_token_cache
            and self._admin_token_cache["expires_at"] > datetime.now()
        ):
            return self._admin_token_cache["token"]

        try:
            admin_token_url = f"{self.settings.keycloak_server_url}/realms/master/protocol/openid-connect/token"

            data = {
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": self.settings.keycloak_admin_username,
                "password": self.settings.keycloak_admin_password,
            }

            headers = {"Content-Type": "application/x-www-form-urlencoded"}

            response = requests.post(
                admin_token_url, data=data, headers=headers, verify=self.ssl_verify
            )
            response.raise_for_status()

            token_data = response.json()
            access_token = token_data["access_token"]
            expires_in = token_data.get("expires_in", 300)

            # Cache the token with expiration (subtract 30 seconds for safety)
            self._admin_token_cache = {
                "token": access_token,
                "expires_at": datetime.now() + timedelta(seconds=expires_in - 30),
            }

            return access_token
        except Exception as e:
            logger.error(f"Failed to get admin token: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to authenticate with Keycloak admin",
            )

    def update_user_profile(self, user_id: str, user_data: Dict[str, Any]) -> bool:
        """
        Update user information in Keycloak using admin API
        """
        try:
            logger.info(f"Updating profile for user: {user_id}")

            # Get admin token
            admin_token = self._get_admin_token()

            # Map the field names to Keycloak's expected format
            keycloak_user_data = {}

            if "first_name" in user_data:
                keycloak_user_data["firstName"] = user_data["first_name"]
            if "last_name" in user_data:
                keycloak_user_data["lastName"] = user_data["last_name"]
            if "email" in user_data:
                keycloak_user_data["email"] = user_data["email"]
            if "username" in user_data:
                keycloak_user_data["username"] = user_data["username"]

            logger.debug(f"Keycloak update data: {keycloak_user_data}")

            # Update user with the correctly formatted data using Keycloak Admin API
            update_url = f"{self.settings.keycloak_server_url}/admin/realms/{self.settings.keycloak_realm}/users/{user_id}"

            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json",
            }

            response = requests.put(
                update_url,
                json=keycloak_user_data,
                headers=headers,
                timeout=10,
                verify=self.ssl_verify,
            )
            response.raise_for_status()

            logger.info(
                f"User profile updated successfully in Keycloak for user ID: {user_id}"
            )
            return True
        except requests.exceptions.Timeout:
            logger.error(f"Timeout while updating user profile for user ID: {user_id}")
            raise HTTPException(
                status_code=status.HTTP_408_REQUEST_TIMEOUT,
                detail="Request timeout while updating user profile",
            )
        except requests.exceptions.RequestException as e:
            logger.error(
                f"Failed to update user info in Keycloak for user {user_id}: {str(e)}"
            )
            if hasattr(e, "response") and e.response is not None:
                logger.error(f"Response status: {e.response.status_code}")
                logger.error(f"Response content: {e.response.text}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to update user info: {str(e)}",
                headers={"WWW-Authenticate": "Bearer"},
            )
        except Exception as e:
            logger.error(
                f"Unexpected error updating user profile for user {user_id}: {str(e)}"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An unexpected error occurred while updating user profile",
                headers={"WWW-Authenticate": "Bearer"},
            )

    def logout(self, refresh_token: str) -> None:
        """
        Logout the user by invalidating the refresh token
        """
        try:
            keycloak_openid.logout(refresh_token=refresh_token)
            logger.info("User logged out successfully")
        except Exception as e:
            logger.error(f"Failed to logout: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to logout",
                headers={"WWW-Authenticate": "Bearer"},
            )

    def health_check(self) -> bool:
        """
        Check if Keycloak is reachable
        """
        try:
            # Try to get the well-known configuration
            well_known = keycloak_openid.well_known()
            return "authorization_endpoint" in well_known
        except Exception as e:
            logger.error(f"Keycloak health check failed: {str(e)}")
            return False

    def _get_client_id(self, admin_token: str, client_name: str) -> str:
        """
        Get the internal client ID for a given client name
        """
        try:
            url = f"{self.settings.keycloak_server_url}/admin/realms/{self.settings.keycloak_realm}/clients"
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json",
            }

            params = {"clientId": client_name}

            response = requests.get(
                url, headers=headers, params=params, verify=self.ssl_verify
            )
            response.raise_for_status()

            clients = response.json()
            if not clients:
                raise ValueError(f"Client '{client_name}' not found")

            return clients[0]["id"]
        except Exception as e:
            logger.error(f"Failed to get client ID for {client_name}: {str(e)}")
            raise

    def _get_client_role(
        self, admin_token: str, client_id: str, role_name: str
    ) -> Dict[str, Any]:
        """
        Get client role information
        """
        try:
            url = f"{self.settings.keycloak_server_url}/admin/realms/{self.settings.keycloak_realm}/clients/{client_id}/roles/{role_name}"
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json",
            }

            response = requests.get(url, headers=headers, verify=self.ssl_verify)
            response.raise_for_status()

            return response.json()
        except Exception as e:
            logger.error(f"Failed to get client role {role_name}: {str(e)}")
            raise

    def _get_user_client_roles(
        self, admin_token: str, user_id: str, client_id: str
    ) -> List[Dict[str, Any]]:
        """
        Get current client roles for a user
        """
        try:
            url = f"{self.settings.keycloak_server_url}/admin/realms/{self.settings.keycloak_realm}/users/{user_id}/role-mappings/clients/{client_id}"
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json",
            }

            response = requests.get(url, headers=headers, verify=self.ssl_verify)
            response.raise_for_status()

            return response.json()
        except Exception as e:
            logger.error(f"Failed to get user client roles: {str(e)}")
            return []

    def _remove_user_client_roles(
        self,
        admin_token: str,
        user_id: str,
        client_id: str,
        roles: List[Dict[str, Any]],
    ) -> None:
        """
        Remove client roles from a user
        """
        if not roles:
            return

        try:
            url = f"{self.settings.keycloak_server_url}/admin/realms/{self.settings.keycloak_realm}/users/{user_id}/role-mappings/clients/{client_id}"
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json",
            }

            response = requests.delete(
                url, json=roles, headers=headers, verify=self.ssl_verify
            )
            response.raise_for_status()

            logger.info(
                f"Successfully removed {len(roles)} client roles from user {user_id}"
            )
        except Exception as e:
            logger.error(f"Failed to remove client roles: {str(e)}")
            raise

    def _assign_user_client_role(
        self, admin_token: str, user_id: str, client_id: str, role: Dict[str, Any]
    ) -> None:
        """
        Assign a client role to a user
        """
        try:
            url = f"{self.settings.keycloak_server_url}/admin/realms/{self.settings.keycloak_realm}/users/{user_id}/role-mappings/clients/{client_id}"
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json",
            }

            response = requests.post(
                url, json=[role], headers=headers, verify=self.ssl_verify
            )
            response.raise_for_status()

            logger.info(
                f"Successfully assigned client role {role['name']} to user {user_id}"
            )
        except Exception as e:
            logger.error(f"Failed to assign client role: {str(e)}")
            raise

    def update_user_role(self, user_id: str, new_role: str) -> None:
        """
        Update a user's role in Keycloak using the proper Admin REST API

        Args:
            user_id: The Keycloak user ID
            new_role: The new role to assign
        """
        try:
            # Get admin token
            admin_token = self._get_admin_token()

            # Get the spoutbreezeAPI client ID
            client_id = self._get_client_id(admin_token, "spoutbreezeAPI")
            logger.info(f"Found client ID: {client_id} for spoutbreezeAPI")

            # Get current client roles for the user
            current_roles = self._get_user_client_roles(admin_token, user_id, client_id)
            logger.info(
                f"Current client roles for user {user_id}: {[role['name'] for role in current_roles]}"
            )

            # Remove all existing client roles for this client
            if current_roles:
                self._remove_user_client_roles(
                    admin_token, user_id, client_id, current_roles
                )
                logger.info(f"Removed existing client roles from user {user_id}")

            # Get the new role information
            new_role_info = self._get_client_role(admin_token, client_id, new_role)
            logger.info(f"Found role info for {new_role}: {new_role_info}")

            # Assign the new client role
            self._assign_user_client_role(
                admin_token, user_id, client_id, new_role_info
            )

            logger.info(
                f"Successfully updated user {user_id} client role to {new_role}"
            )

        except Exception as e:
            logger.error(f"Failed to update user role in Keycloak: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to update user role: {str(e)}",
            )
