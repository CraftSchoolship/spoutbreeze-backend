-- Script to grant unlimited access to a user by email
-- Usage: Replace 'user@example.com' with the actual email address

-- Check if user exists and view their current status
SELECT 
    id,
    email,
    username,
    first_name,
    last_name,
    unlimited_access,
    is_active
FROM users 
WHERE email = 'mohamed@mohamed.com';

-- Grant unlimited access (uncomment to execute)
-- UPDATE users 
-- SET unlimited_access = true 
-- WHERE email = 'mohamed@mohamed.com';

-- Verify the change (uncomment to execute after update)
-- SELECT 
--     email,
--     username,
--     unlimited_access
-- FROM users 
-- WHERE email = 'mohamed@mohamed.com';

-- To revoke unlimited access (if needed)
-- UPDATE users 
-- SET unlimited_access = false 
-- WHERE email = 'mohamed@mohamed.com';
