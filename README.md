# IOST Wallet Monitor

A production-ready FastAPI backend that monitors an IOST wallet using IOSTScan and sends transaction alerts to a Discord channel.

## Features

- 🔄 **Real-time monitoring** - Polls IOSTScan every 25-30 seconds for new transactions
- 🤖 **Discord integration** - Sends formatted alerts to your Discord channel
- 💾 **Persistent state** - Tracks processed transactions to prevent duplicate alerts
- 🔒 **Robust error handling** - Retries on API failures, handles rate limits
- 📊 **Status endpoints** - Health checks and monitoring status via REST API

## Quick Start

### 1. Clone and Setup

```bash
cd "Discord Bot IOST Fetch"
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your values
```

Required environment variables:
- `DISCORD_BOT_TOKEN` - Your Discord bot token
- `DISCORD_CHANNEL_ID` - Target channel ID for alerts

### 3. Setup Discord Bot

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application or use existing (Application ID: `1465356723765379307`)
3. Go to **Bot** section and copy the token
4. Enable these permissions when creating invite link:
   - Send Messages
   - Embed Links
5. Invite bot to your server using OAuth2 URL Generator

### 4. Run the Application

```bash
uvicorn app.main:app --reload
```

The server will start at `http://localhost:8000`

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | Basic service info |
| `GET /health` | Health check |
| `GET /status` | Detailed monitoring status |
| `GET /docs` | Swagger API documentation |

## Project Structure

```
├── app/
│   ├── __init__.py
│   ├── config.py        # Environment configuration
│   ├── storage.py       # Transaction state persistence
│   ├── iostscan.py      # IOSTScan API client
│   ├── discord_bot.py   # Discord integration
│   └── main.py          # FastAPI application
├── data/
│   └── last_tx.json     # Processed transaction state (auto-created)
├── .env.example         # Environment template
├── requirements.txt     # Python dependencies
└── README.md
```

## Discord Message Format

When a new transaction is detected:

```
🚨 IOST Received!
Wallet: inwaliost
From: sender_address
To: inwaliost
Amount: 100 IOST
Action: transfer
Tx: https://www.iostscan.com/tx/abc123...
```

## Configuration Options

| Variable | Default | Description |
|----------|---------|-------------|
| `IOST_WALLET_ADDRESS` | `inwaliost` | Wallet to monitor |
| `POLL_INTERVAL_SECONDS` | `25` | Polling frequency |
| `STORAGE_FILE_PATH` | `data/last_tx.json` | State file location |
| `IOSTSCAN_API_TIMEOUT` | `30` | API request timeout |
| `MAX_RETRIES` | `3` | Retry attempts on failure |

## Production Deployment

For production, consider:

1. **Process Manager**: Use `supervisord`, `systemd`, or Docker
2. **Reverse Proxy**: Put behind nginx for SSL termination
3. **Monitoring**: Add logging aggregation (e.g., Loki, CloudWatch)

Example with Docker:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

## Troubleshooting

### Bot not sending messages
- Verify `DISCORD_BOT_TOKEN` is correct
- Check bot has permissions in the target channel
- Ensure `DISCORD_CHANNEL_ID` is correct (enable Developer Mode to copy IDs)

### No transactions detected
- The IOSTScan API may return empty if no recent activity
- Check `/status` endpoint to verify polling is working
- Review logs for API errors

### Duplicate alerts
- Delete `data/last_tx.json` to reset state (will re-alert recent transactions)

## License

MIT
