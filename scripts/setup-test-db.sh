#!/bin/bash
set -e

echo "🐘 Setting up test database..."

# Stop and remove existing containers and volumes
echo "🧹 Cleaning up existing containers..."
docker compose down -v 2>/dev/null || true

# Start PostgreSQL container
echo "🚀 Starting PostgreSQL..."
docker compose up -d postgres

# Wait for PostgreSQL health check to report healthy
echo "⏳ Waiting for PostgreSQL to be ready..."
for i in {1..60}; do
    STATUS=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' mayflower-test-db 2>/dev/null || echo "unknown")
    if [ "$STATUS" = "healthy" ]; then
        echo "✓ PostgreSQL is ready!"
        break
    fi
    if [ $i -eq 60 ]; then
        echo "❌ PostgreSQL failed to become healthy"
        exit 1
    fi
    sleep 1
done

# Ensure the test database exists before applying migrations
echo "🛠️ Ensuring test database exists..."
if ! docker compose exec -T postgres psql -U postgres -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = 'mayflower_test';" | grep -q 1; then
    docker compose exec -T postgres psql -U postgres -c "CREATE DATABASE mayflower_test;"
    echo "✓ Created database mayflower_test"
else
    echo "✓ Database mayflower_test already exists"
fi

# Run migrations
echo "📝 Running migrations..."
docker compose exec -T postgres psql -U postgres -d mayflower_test < migrations/001_sandbox_schema.sql

echo ""
echo "✅ Test database setup complete!"
echo ""
echo "Database connection:"
echo "  Host: localhost"
echo "  Port: 5433"
echo "  Database: mayflower_test"
echo "  User: postgres"
echo "  Password: postgres"
echo ""
echo "Commands:"
echo "  Stop: docker compose down"
echo "  Clean: docker compose down -v"
