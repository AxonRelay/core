#!/bin/bash
# Run database migrations

set -e

echo "Running Alembic migrations..."
alembic upgrade head

echo "Migrations completed successfully!"
