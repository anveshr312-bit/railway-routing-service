---
title: Railway Routing Service
emoji: 🚂
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
---

# Railway Routing Service

This is the FastAPI backend service that performs spatial routing algorithms across the Indian railway network.

## Deployment on Ubuntu VPS

### Prerequisites
1. Ensure `docker` and `docker-compose` are installed on your Ubuntu VPS.
2. Clone this repository to your VPS.

### Option 1: Run with Docker (Recommended)

1. Build the Docker image:
```bash
docker build -t railway-routing-service .
```

2. Run the container:
```bash
docker run -d --name railway-router -p 8000:8000 railway-routing-service
```

### Option 2: Systemd & Nginx deployment

If you prefer to run it without Docker, using standard Python:

1. Install system dependencies:
```bash
sudo apt update
sudo apt install python3.12-venv python3.12-dev build-essential libgdal-dev libgeos-dev libproj-dev libspatialindex-dev
```

2. Create a virtual environment and install requirements:
```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

3. Create a Systemd service (`/etc/systemd/system/railway-router.service`):
```ini
[Unit]
Description=Railway Routing FastAPI Application
After=network.target

[Service]
User=ubuntu
Group=www-data
WorkingDirectory=/home/ubuntu/railway-routing-service
Environment="PATH=/home/ubuntu/railway-routing-service/.venv/bin"
Environment="RAILWAYS_GPKG=/home/ubuntu/railway-routing-service/railways.gpkg"
ExecStart=/home/ubuntu/railway-routing-service/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000

[Install]
WantedBy=multi-user.target
```

4. Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl start railway-router
sudo systemctl enable railway-router
```

5. Configure Nginx Reverse Proxy (`/etc/nginx/sites-available/railway-router`):
```nginx
server {
    listen 80;
    server_name yourdomain.com; # Replace with your IP or Domain

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

6. Enable Nginx config:
```bash
sudo ln -s /etc/nginx/sites-available/railway-router /etc/nginx/sites-enabled/
sudo systemctl restart nginx
```
