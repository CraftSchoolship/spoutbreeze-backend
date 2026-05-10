import asyncio
from datetime import datetime, timedelta
from typing import Any

import httpx
from fastapi import HTTPException, status
from jose import jwt
from jose.exceptions import JWTClaimsError

from app.config.logger_config import logger
from app.config.settings import get_keycloak_openid, get_settings, resolve_ssl_verify


class AuthService:
    """
    Service for authentication and authorization operations
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.keycloak_client_id = self.settings.keycloak_client_id

        self._public_key: str | None = None

        self._admin_token_cache: dict | None = None

        # SSL verification for HTTP clients
        self.ssl_verify = self._get_ssl_verify()

    async def _get_public_key(self) -> str:
        """Lazy-load the Keycloak public key on first use (off the event loop)."""
        if self._public_key is None:
            raw_key = await asyncio.to_thread(get_keycloak_openid().public_key)
            if not raw_key.startswith("-----BEGIN"):
                self._public_key = f"-----BEGIN PUBLIC KEY-----\n{raw_key}\n-----END PUBLIC KEY-----"
            else:
                self._public_key = raw_key
        return self._public_key

    def _get_ssl_verify(self) -> str | bool:
        """Resolve SSL verification from settings (no filesystem probing).

        Delegates to `resolve_ssl_verify` so the policy lives in one place
        and the previous silent-fallback-to-`verify=False` behavior cannot
        return.
        """
        return resolve_ssl_verify(self.settings)

    def _http_client(self) -> httpx.AsyncClient:
        """Build an httpx.AsyncClient with the configured SSL verification."""
        return httpx.AsyncClient(verify=self.ssl_verify, timeout=10.0)

    async def validate_token(self, token: str) -> dict[str, Any]:
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
            public_key = await self._get_public_key()

            # Decode with python-jose. Audience is enforced so that tokens
            # minted for sibling clients in the same Keycloak realm are
            # rejected — without this check, any realm-signed token would
            # be accepted regardless of who it was issued for.
            try:
                payload = jwt.decode(
                    token,
                    public_key,
                    algorithms=["RS256"],
                    audience=self.keycloak_client_id,
                    options={
                        "verify_aud": True,
                        "verify_exp": True,
                        "verify_iat": True,
                        "verify_nbf": True,
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

                return payload

            except JWTClaimsError as e:
                # Surfaces audience-claim mismatches distinctly so a
                # Keycloak audience-mapper misconfiguration is easy to
                # spot in logs (it shows up as the same error on every
                # request).
                logger.error(
                    f"JWT claims rejected (audience mismatch likely; "
                    f"expected aud={self.keycloak_client_id!r}): {e}"
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token: claim verification failed",
                    headers={"WWW-Authenticate": "Bearer"},
                )
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

    async def exchange_token(self, code: str, redirect_uri: str, code_verifier: str) -> dict[str, Any]:
        """
        Exchange authorization code for tokens
        """
        try:
            token = await asyncio.to_thread(
                get_keycloak_openid().token,
                grant_type="authorization_code",
                code=code,
                redirect_uri=redirect_uri,
                code_verifier=code_verifier,
                scope="openid profile email account",
            )
            return token
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to exchange token: {str(e)}",
                headers={"WWW-Authenticate": "Bearer"},
            )

    async def refresh_token(self, refresh_token: str) -> dict[str, Any]:
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
            token_response = await asyncio.to_thread(get_keycloak_openid().refresh_token, refresh_token)

            # Get user info with the new token
            user_info = await self.get_user_info(token_response["access_token"])

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

    async def get_user_info(self, access_token: str) -> dict[str, Any]:
        """
        Get user information from Keycloak using the access token
        """
        try:
            user_info = await asyncio.to_thread(get_keycloak_openid().userinfo, access_token)
            return user_info
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Failed to get user info",
                headers={"WWW-Authenticate": "Bearer"},
            )

    async def _get_admin_token(self, client: httpx.AsyncClient | None = None) -> str:
        """
        Get admin token from Keycloak with caching
        """
        # Check if we have a cached token that's still valid
        if self._admin_token_cache and self._admin_token_cache["expires_at"] > datetime.now():
            return self._admin_token_cache["token"]

        admin_token_url = f"{self.settings.keycloak_server_url}/realms/master/protocol/openid-connect/token"

        data = {
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": self.settings.keycloak_admin_username,
            "password": self.settings.keycloak_admin_password,
        }

        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        try:
            if client is None:
                async with self._http_client() as new_client:
                    response = await new_client.post(admin_token_url, data=data, headers=headers)
            else:
                response = await client.post(admin_token_url, data=data, headers=headers)
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

    async def update_user_profile(self, user_id: str, user_data: dict[str, Any]) -> bool:
        """
        Update user information in Keycloak using admin API
        """
        try:
            logger.info(f"Updating profile for user: {user_id}")

            async with self._http_client() as client:
                # Get admin token (reuses the same client connection pool)
                admin_token = await self._get_admin_token(client=client)

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
                update_url = (
                    f"{self.settings.keycloak_server_url}/admin/realms/"
                    f"{self.settings.keycloak_realm}/users/{user_id}"
                )

                headers = {
                    "Authorization": f"Bearer {admin_token}",
                    "Content-Type": "application/json",
                }

                response = await client.put(update_url, json=keycloak_user_data, headers=headers)
                response.raise_for_status()

            logger.info(f"User profile updated successfully in Keycloak for user ID: {user_id}")
            return True
        except httpx.TimeoutException:
            logger.error(f"Timeout while updating user profile for user ID: {user_id}")
            raise HTTPException(
                status_code=status.HTTP_408_REQUEST_TIMEOUT,
                detail="Request timeout while updating user profile",
            )
        except httpx.HTTPError as e:
            logger.error(f"Failed to update user info in Keycloak for user {user_id}: {str(e)}")
            response_obj = getattr(e, "response", None)
            if response_obj is not None:
                logger.error(f"Response status: {response_obj.status_code}")
                logger.error(f"Response content: {response_obj.text}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to update user info: {str(e)}",
                headers={"WWW-Authenticate": "Bearer"},
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Unexpected error updating user profile for user {user_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An unexpected error occurred while updating user profile",
                headers={"WWW-Authenticate": "Bearer"},
            )

    async def logout(self, refresh_token: str) -> None:
        """
        Logout the user by invalidating the refresh token
        """
        try:
            await asyncio.to_thread(get_keycloak_openid().logout, refresh_token)
            logger.info("User logged out successfully")
        except Exception as e:
            logger.error(f"Failed to logout: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to logout",
                headers={"WWW-Authenticate": "Bearer"},
            )

    async def health_check(self) -> bool:
        """
        Check if Keycloak is reachable
        """
        try:
            # Try to get the well-known configuration
            well_known = await asyncio.to_thread(get_keycloak_openid().well_known)
            return "authorization_endpoint" in well_known
        except Exception as e:
            logger.error(f"Keycloak health check failed: {str(e)}")
            return False

    async def _get_client_id(self, client: httpx.AsyncClient, admin_token: str, client_name: str) -> str:
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

            response = await client.get(url, headers=headers, params=params)
            response.raise_for_status()

            clients = response.json()
            if not clients:
                raise ValueError(f"Client '{client_name}' not found")

            return clients[0]["id"]
        except Exception as e:
            logger.error(f"Failed to get client ID for {client_name}: {str(e)}")
            raise

    async def _get_client_role(
        self, client: httpx.AsyncClient, admin_token: str, client_id: str, role_name: str
    ) -> dict[str, Any]:
        """
        Get client role information
        """
        try:
            url = (
                f"{self.settings.keycloak_server_url}/admin/realms/"
                f"{self.settings.keycloak_realm}/clients/{client_id}/roles/{role_name}"
            )
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json",
            }

            response = await client.get(url, headers=headers)
            response.raise_for_status()

            return response.json()
        except Exception as e:
            logger.error(f"Failed to get client role {role_name}: {str(e)}")
            raise

    async def _get_user_client_roles(
        self, client: httpx.AsyncClient, admin_token: str, user_id: str, client_id: str
    ) -> list[dict[str, Any]]:
        """
        Get current client roles for a user
        """
        try:
            url = (
                f"{self.settings.keycloak_server_url}/admin/realms/"
                f"{self.settings.keycloak_realm}/users/{user_id}/role-mappings/clients/{client_id}"
            )
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json",
            }

            response = await client.get(url, headers=headers)
            response.raise_for_status()

            return response.json()
        except Exception as e:
            logger.error(f"Failed to get user client roles: {str(e)}")
            return []

    async def _remove_user_client_roles(
        self,
        client: httpx.AsyncClient,
        admin_token: str,
        user_id: str,
        client_id: str,
        roles: list[dict[str, Any]],
    ) -> None:
        """
        Remove client roles from a user
        """
        if not roles:
            return

        try:
            url = (
                f"{self.settings.keycloak_server_url}/admin/realms/"
                f"{self.settings.keycloak_realm}/users/{user_id}/role-mappings/clients/{client_id}"
            )
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json",
            }

            response = await client.request("DELETE", url, json=roles, headers=headers)
            response.raise_for_status()

            logger.info(f"Successfully removed {len(roles)} client roles from user {user_id}")
        except Exception as e:
            logger.error(f"Failed to remove client roles: {str(e)}")
            raise

    async def _assign_user_client_role(
        self,
        client: httpx.AsyncClient,
        admin_token: str,
        user_id: str,
        client_id: str,
        role: dict[str, Any],
    ) -> None:
        """
        Assign a client role to a user
        """
        try:
            url = (
                f"{self.settings.keycloak_server_url}/admin/realms/"
                f"{self.settings.keycloak_realm}/users/{user_id}/role-mappings/clients/{client_id}"
            )
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json",
            }

            response = await client.post(url, json=[role], headers=headers)
            response.raise_for_status()

            logger.info(f"Successfully assigned client role {role['name']} to user {user_id}")
        except Exception as e:
            logger.error(f"Failed to assign client role: {str(e)}")
            raise

    async def update_user_role(self, user_id: str, new_role: str) -> None:
        """
        Update a user's role in Keycloak using the proper Admin REST API

        Args:
            user_id: The Keycloak user ID
            new_role: The new role to assign
        """
        try:
            async with self._http_client() as client:
                admin_token = await self._get_admin_token(client=client)

                # Get the spoutbreezeAPI client ID
                client_id = await self._get_client_id(client, admin_token, "spoutbreezeAPI")
                logger.info(f"Found client ID: {client_id} for spoutbreezeAPI")

                # Get current client roles for the user
                current_roles = await self._get_user_client_roles(client, admin_token, user_id, client_id)
                logger.info(
                    f"Current client roles for user {user_id}: {[role['name'] for role in current_roles]}"
                )

                # Remove all existing client roles for this client
                if current_roles:
                    await self._remove_user_client_roles(client, admin_token, user_id, client_id, current_roles)
                    logger.info(f"Removed existing client roles from user {user_id}")

                # Get the new role information
                new_role_info = await self._get_client_role(client, admin_token, client_id, new_role)
                logger.info(f"Found role info for {new_role}: {new_role_info}")

                # Assign the new client role
                await self._assign_user_client_role(client, admin_token, user_id, client_id, new_role_info)

            logger.info(f"Successfully updated user {user_id} client role to {new_role}")

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to update user role in Keycloak: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to update user role: {str(e)}",
            )

    async def delete_user(self, user_id: str) -> bool:
        """
        Permanently delete a user from Keycloak using the Admin REST API.

        Args:
            user_id: The Keycloak user ID (sub claim)

        Returns:
            True if the user was deleted successfully

        Raises:
            HTTPException: If the deletion fails
        """
        try:
            logger.info(f"Deleting user from Keycloak: {user_id}")

            async with self._http_client() as client:
                admin_token = await self._get_admin_token(client=client)

                delete_url = (
                    f"{self.settings.keycloak_server_url}/admin/realms/"
                    f"{self.settings.keycloak_realm}/users/{user_id}"
                )

                headers = {
                    "Authorization": f"Bearer {admin_token}",
                    "Content-Type": "application/json",
                }

                response = await client.delete(delete_url, headers=headers)
                response.raise_for_status()

            logger.info(f"User {user_id} deleted successfully from Keycloak")
            return True

        except httpx.TimeoutException:
            logger.error(f"Timeout while deleting user {user_id} from Keycloak")
            raise HTTPException(
                status_code=status.HTTP_408_REQUEST_TIMEOUT,
                detail="Request timeout while deleting user from Keycloak",
            )
        except httpx.HTTPError as e:
            logger.error(f"Failed to delete user {user_id} from Keycloak: {str(e)}")
            response_obj = getattr(e, "response", None)
            if response_obj is not None:
                logger.error(f"Response status: {response_obj.status_code}")
                logger.error(f"Response content: {response_obj.text}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to delete user from Keycloak: {str(e)}",
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Unexpected error deleting user {user_id} from Keycloak: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An unexpected error occurred while deleting user from Keycloak",
            )
