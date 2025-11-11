#!/bin/bash

# Telegram Ollama Bot Setup Script
set -e

echo "==================================="
echo "Telegram Ollama Bot Setup"
echo "==================================="
echo ""

# Check Python version
echo "Checking Python version..."
python_version=$(python3 --version 2>&1 | grep -Po '(?<=Python )\d+\.\d+')
required_version="3.12"

if [ "$(printf '%s\n' "$required_version" "$python_version" | sort -V | head -n1)" != "$required_version" ]; then
    echo "Error: Python 3.12+ is required, but Python $python_version is installed."
    exit 1
fi
echo "✓ Python $python_version found"

# Check for Poetry
echo "Checking for Poetry..."
if ! command -v poetry &> /dev/null; then
    echo "Poetry not found. Installing..."
    curl -sSL https://install.python-poetry.org | python3 -
    export PATH="$HOME/.local/bin:$PATH"
fi
echo "✓ Poetry is installed"

# Check for Docker (optional)
echo "Checking for Docker..."
if command -v docker &> /dev/null; then
    echo "✓ Docker is installed"
else
    echo "⚠ Docker not found (optional, but recommended for deployment)"
fi

# Check for Ollama
echo "Checking for Ollama..."
if command -v ollama &> /dev/null; then
    echo "✓ Ollama is installed"
    echo "Available models:"
    ollama list 2>/dev/null || echo "  (Ollama service not running)"
else
    echo "⚠ Ollama not found. Please install from: https://ollama.ai"
    echo "  After installation, pull models with: ollama pull llama2"
fi

# Create necessary directories
echo ""
echo "Creating directories..."
mkdir -p data backups logs
echo "✓ Directories created"

# Setup environment file
echo ""
if [ ! -f .env ]; then
    echo "Setting up environment configuration..."
    cp .env.example .env
    echo "✓ Created .env file from template"
    echo ""
    echo "⚠ IMPORTANT: Please edit .env and add:"
    echo "  1. Your Telegram Bot Token (from @BotFather)"
    echo "  2. Your Telegram User ID (for admin access)"
    echo "  3. Ollama host if not using localhost"
    echo ""
    read -p "Would you like to edit .env now? (y/n) " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        ${EDITOR:-nano} .env
    fi
else
    echo "✓ .env file already exists"
fi

# Install dependencies
echo ""
echo "Installing Python dependencies..."
poetry install
echo "✓ Dependencies installed"

# Database setup
echo ""
echo "Setting up database..."
poetry run python -c "
import asyncio
from src.bot.database import init_database
asyncio.run(init_database())
print('✓ Database initialized')
"

# Offer to pull common Ollama models
echo ""
echo "==================================="
echo "Setup Complete!"
echo "==================================="
echo ""
echo "Recommended Ollama models to install:"
echo "  ollama pull llama2       # General purpose"
echo "  ollama pull mistral      # Fast and efficient"
echo "  ollama pull codellama    # Code generation"
echo ""
echo "To start the bot:"
echo "  Using Poetry:  poetry run python -m src.bot.main"
echo "  Using Docker:  make run"
echo "  Using Make:    make dev"
echo ""
echo "For help and commands:"
echo "  make help"
echo ""
echo "Documentation: README.md"
echo ""

# Offer to start the bot
read -p "Would you like to start the bot now? (y/n) " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Starting bot..."
    poetry run python -m src.bot.main
fi
