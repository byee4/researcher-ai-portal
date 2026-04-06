#!/usr/bin/env sh
# Local development environment template for researcher-ai-portal.
# Copy values into your shell or .env file before running the portal.

export PLATFORM='DEV'
export DJANGO_SECRET_KEY='change-me'
export DJANGO_DEBUG='True'
export DJANGO_ALLOWED_HOSTS='localhost,127.0.0.1,0.0.0.0'

# Optional auth integration:
export GLOBUS_CLIENT_ID=''
export GLOBUS_CLIENT_SECRET=''
export GLOBUS_ADMIN_GROUP=''
export SOCIAL_AUTH_GLOBUS_REDIRECT_URI=''

# Optional runtime dependencies:
export REDIS_URL='redis://localhost:6379/0'
