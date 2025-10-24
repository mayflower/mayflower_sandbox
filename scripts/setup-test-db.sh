#!/bin/bash
set -e

echo "🐘 Setting up test database..."

# Stop and remove existing containers and volumes
echo "🧹 Cleaning up existing containers..."
docker compose down -v 2>/dev/null || true

# Start PostgreSQL container
echo "🚀 Starting PostgreSQL..."
docker compose up -d postgres

# Wait for PostgreSQL to be ready
echo "⏳ Waiting for PostgreSQL to be ready..."
for i in {1..30}; do
    if docker compose exec postgres pg_isready -U postgres > /dev/null 2>&1; then
        echo "✓ PostgreSQL is ready!"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "❌ PostgreSQL failed to start"
        exit 1
    fi
    sleep 1
done

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
