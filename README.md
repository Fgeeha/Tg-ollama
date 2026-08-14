# Telegram Bot with Ollama Integration

A powerful Telegram bot that integrates with Ollama for AI-powered conversations, featuring user management, model selection, and comprehensive admin controls.

## Features

### Core Functionality
- 🤖 **Ollama Integration**: Seamlessly interact with locally or remotely hosted Ollama models
- 💬 **Streaming Responses**: Real-time message streaming for better user experience
- 🔄 **Model Selection**: Choose from available pre-downloaded Ollama models
- 🖼️ **Image Input**: Send a photo (with an optional caption) to a vision-capable model such as `llava`; the bot checks the selected model's `vision` capability first and tells you if it is unsupported
- 📝 **Conversation Context**: Maintains conversation history with intelligent context management
- ⏱️ **Rate Limiting**: Configurable rate limiting to prevent abuse

### Access Control
- 👑 **Admin Management**: Full admin control over user authorization
- 🔒 **Test Mode**: Admin-only interaction mode for testing
- 👥 **User Authorization**: Database-backed user management system
- 📊 **Usage Statistics**: Track model usage and response times

### Technical Features
- 🐳 **Docker Support**: Full containerization with multi-stage builds
- 📦 **Poetry Package Management**: Modern Python dependency management
- 🗄️ **SQLite/PostgreSQL**: Flexible database options
- 📈 **Health Checks**: Built-in health monitoring endpoints
- 📝 **Structured Logging**: JSON-formatted logs for production

## Requirements

- Python 3.12+
- Docker & Docker Compose (optional)
- Ollama installed and running
- Telegram Bot Token (from @BotFather)

## Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/fgeeha/tg-ollama.git
cd tg-ollama
```

### 2. Configure Environment

Copy the example environment file and configure:

```bash
cp .env.example .env
```

Edit `.env` with your configuration:

```env
# Required
BOT_TOKEN=your_telegram_bot_token
ADMIN_ID=your_telegram_user_id
OLLAMA_HOST=http://localhost:11434

# Optional
DATABASE_URL=sqlite:///data/bot.db
TEST_MODE=false
LOG_LEVEL=INFO
```

### 3. Install Ollama Models

Ensure Ollama is running and pull desired models:

```bash
ollama pull llama2
ollama pull codellama
ollama pull mistral
```

### 4. Run the Bot

#### Using Docker (Recommended)

```bash
# Build and run
make run

# View logs
make logs

# Stop
make stop
```

#### Using Poetry

```bash
# Install dependencies
poetry install

# Run the bot
poetry run python -m src.bot.main
```

## Usage

### User Commands

| Command | Description |
|---------|-------------|
| `/start` | Start the bot and see welcome message |
| `/help` | Show available commands |
| `/status` | Check bot and Ollama status |
| `/models` | List and select available models |
| `/switch_model <name>` | Switch to a specific model |
| `/current_model` | Show currently selected model |
| `/model_info [name]` | Get detailed model information |
| `/clear` | Clear conversation context |
| `/regenerate` | Regenerate the last response |
| `/history` | Show recent conversation history |

### Admin Commands

| Command | Description |
|---------|-------------|
| `/add_user <user_id>` | Authorize a new user |
| `/remove_user <user_id>` | Revoke user access |
| `/list_users` | List all authorized users |
| `/test_mode [on/off]` | Toggle test mode |
| `/stats` | View usage statistics |
| `/clear_history [user_id/all]` | Clear conversation history |
| `/broadcast <message>` | Send message to all users |

### Chatting

Simply send any message to the bot to start chatting with the selected AI model. The bot maintains context across messages for coherent conversations.

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BOT_TOKEN` | - | Telegram bot token (required) |
| `ADMIN_ID` | - | Admin Telegram user ID (required) |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama API endpoint |
| `OLLAMA_TIMEOUT` | `60` | Ollama request timeout (seconds) |
| `DATABASE_URL` | `sqlite:///data/bot.db` | Database connection string |
| `TEST_MODE` | `false` | Enable test mode (admin only) |
| `LOG_LEVEL` | `INFO` | Logging level |
| `MAX_CONTEXT_LENGTH` | `4096` | Maximum context length |
| `DEFAULT_MODEL` | `llama2` | Default Ollama model |
| `RATE_LIMIT_MESSAGES` | `10` | Messages per rate limit window |
| `RATE_LIMIT_WINDOW` | `60` | Rate limit window (seconds) |
| `HEALTH_CHECK_ENABLED` | `true` | Enable health check endpoint |
| `HEALTH_CHECK_PORT` | `8080` | Health check server port |

### Database Configuration

#### SQLite (Default)
```env
DATABASE_URL=sqlite:///data/bot.db
```

#### PostgreSQL
```env
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/tg_ollama_bot
```

## Development

### Project Structure

```
tg-ollama-bot/
├── src/
│   ├── bot/
│   │   ├── main.py              # Entry point
│   │   ├── config.py            # Configuration
│   │   ├── decorators.py        # Auth & rate limiting
│   │   ├── database/            # Database models & connection
│   │   │   ├── models.py
│   │   │   └── connection.py
│   │   ├── handlers/            # Command handlers
│   │   │   ├── admin.py
│   │   │   ├── chat.py
│   │   │   ├── common.py
│   │   │   └── models.py
│   │   └── utils/               # Utilities
│   │       ├── context.py       # Conversation context
│   │       ├── health.py        # Health checks
│   │       ├── logging.py       # Logging setup
│   │       └── ollama.py        # Ollama client
│   └── tests/                   # Test suite
├── Dockerfile                   # Docker configuration
├── Makefile                     # Build & deploy commands
├── pyproject.toml              # Poetry configuration
└── README.md                   # Documentation
```

### Running Tests

```bash
# Run all tests
make test

# Run with coverage
poetry run pytest --cov=src --cov-report=term-missing

# Run specific test
poetry run pytest src/tests/test_bot.py::TestOllamaClient
```

### Code Quality

```bash
# Format code
make format

# Run linting
make lint

# Type checking
poetry run mypy src/
```

### Database Migrations

```bash
# Create migration
make migrate-create

# Apply migrations
make migrate

# Rollback
make migrate-rollback
```

## Deployment

### Docker Deployment

1. Build the image:
```bash
make build
```

2. Run with docker-compose:
```yaml
services:
  bot:
    build: .
    env_file: .env
    volumes:
      - ./data:/app/data
    restart: unless-stopped
    
  ollama:
    image: ollama/ollama
    ports:
      - "11434:11434"
    volumes:
      - ollama_data:/root/.ollama
      
volumes:
  ollama_data:
```

### Production Considerations

1. **Security**:
   - Use environment variables for sensitive data
   - Enable HTTPS for Ollama if exposed
   - Implement proper firewall rules
   - Regular security updates

2. **Monitoring**:
   - Health endpoint: `http://localhost:8080/health`
   - Metrics endpoint: `http://localhost:8080/metrics`
   - Configure Prometheus/Grafana for metrics
   - Set up error tracking (Sentry)

3. **Backup**:
```bash
# Backup database
make backup

# Restore from backup
make restore
```

4. **Scaling**:
   - Use PostgreSQL for production
   - Implement Redis for caching
   - Consider message queue for high load
   - Horizontal scaling with load balancer

## Troubleshooting

### Common Issues

1. **Ollama Connection Failed**
   - Ensure Ollama is running: `ollama serve`
   - Check OLLAMA_HOST configuration
   - Verify network connectivity

2. **Model Not Found**
   - Pull the model: `ollama pull <model_name>`
   - Check available models: `ollama list`

3. **Database Errors**
   - Check DATABASE_URL configuration
   - Ensure write permissions for SQLite
   - Run migrations: `make migrate`

4. **Rate Limiting**
   - Adjust RATE_LIMIT_MESSAGES and RATE_LIMIT_WINDOW
   - Admin users bypass rate limits

### Debug Mode

Enable debug logging:
```env
LOG_LEVEL=DEBUG
```

View container logs:
```bash
make logs
```


