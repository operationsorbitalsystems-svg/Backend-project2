# Invoice Parser Backend - Deployment Checklist

## Pre-Deployment Checklist

- [ ] All endpoints tested locally
- [ ] Error handling implemented
- [ ] Logging configured
- [ ] Environment variables documented
- [ ] Security review completed
- [ ] Performance tested with 10+ files
- [ ] Frontend integration tested
- [ ] API documentation reviewed

---

## Environment Setup

### Production Configuration (.env)

```env
# Server
DEBUG=false
LOG_LEVEL=WARNING
HOST=0.0.0.0
PORT=8000

# Mistral AI (required for production)
MISTRAL_API_KEY=sk_your_production_key_here

# CORS (update to your domain)
CORS_ORIGINS=["https://yourdomain.com","https://app.yourdomain.com"]

# Sessions
REDIS_ENABLED=true
REDIS_URL=redis://redis-server:6379
SESSION_TIMEOUT_HOURS=2

# Files
MAX_FILE_SIZE=100000000
MAX_FILES_PER_BATCH=20
```

---

## Local Testing Checklist

```bash
# 1. Virtual environment
[ ] python -m venv venv
[ ] source venv/bin/activate

# 2. Dependencies
[ ] pip install -r requirements.txt

# 3. Environment
[ ] cp .env.example .env
[ ] Update .env with test values
[ ] Verify MISTRAL_API_KEY set

# 4. Startup
[ ] python main.py
[ ] Check "Application startup" log message
[ ] Verify port 8000 open: netstat -an | grep 8000

# 5. Health check
[ ] curl http://localhost:8000/health
[ ] Response includes "healthy"

# 6. API tests
[ ] Create session: POST /api/sessions
[ ] Upload files: POST /api/sessions/{batch_id}/upload
[ ] Check status: GET /api/sessions/{batch_id}/status
[ ] Verify response structure matches spec

# 7. Logging
[ ] logs/app.log created
[ ] Logs contain timestamped entries
[ ] No errors in logs

# 8. File handling
[ ] /tmp/invoice_uploads created
[ ] Batch directory created on upload
[ ] Files saved correctly
[ ] JSON results generated
[ ] Auto-cleanup after 4 hours
```

---

## Docker Deployment

### Dockerfile

Create `Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Create logs directory
RUN mkdir -p logs

# Environment
ENV HOST=0.0.0.0
ENV PORT=8000
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["python", "main.py"]
```

### docker-compose.yml

```yaml
version: '3.8'

services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

  backend:
    build: .
    ports:
      - "8000:8000"
    environment:
      DEBUG: "false"
      LOG_LEVEL: "INFO"
      MISTRAL_API_KEY: ${MISTRAL_API_KEY}
      REDIS_ENABLED: "true"
      REDIS_URL: "redis://redis:6379"
      CORS_ORIGINS: '["https://yourdomain.com"]'
    depends_on:
      redis:
        condition: service_healthy
    volumes:
      - ./logs:/app/logs
      - /tmp/invoice_uploads:/tmp/invoice_uploads
    restart: unless-stopped

volumes:
  redis_data:
```

### Build & Run

```bash
# Build image
docker build -t invoice-parser-backend:latest .

# Run with Docker
docker run -p 8000:8000 \
  -e MISTRAL_API_KEY=sk_your_key \
  -e REDIS_ENABLED=false \
  invoice-parser-backend:latest

# Or with docker-compose (includes Redis)
docker-compose up -d

# Check logs
docker-compose logs -f backend
```

---

## Kubernetes Deployment

### deployment.yaml

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: invoice-parser-backend
spec:
  replicas: 3
  selector:
    matchLabels:
      app: invoice-parser-backend
  template:
    metadata:
      labels:
        app: invoice-parser-backend
    spec:
      containers:
      - name: backend
        image: invoice-parser-backend:latest
        ports:
        - containerPort: 8000
        env:
        - name: DEBUG
          value: "false"
        - name: MISTRAL_API_KEY
          valueFrom:
            secretKeyRef:
              name: api-keys
              key: mistral
        - name: REDIS_ENABLED
          value: "true"
        - name: REDIS_URL
          value: "redis://redis-service:6379"
        resources:
          requests:
            memory: "256Mi"
            cpu: "250m"
          limits:
            memory: "512Mi"
            cpu: "500m"
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 5

---
apiVersion: v1
kind: Service
metadata:
  name: invoice-parser-backend
spec:
  selector:
    app: invoice-parser-backend
  ports:
  - protocol: TCP
    port: 80
    targetPort: 8000
  type: LoadBalancer
```

### Deploy to Kubernetes

```bash
# Create namespace
kubectl create namespace invoice-parser

# Create secrets
kubectl create secret generic api-keys \
  --from-literal=mistral=sk_your_key \
  -n invoice-parser

# Deploy
kubectl apply -f deployment.yaml -n invoice-parser

# Check status
kubectl get pods -n invoice-parser
kubectl logs -f deployment/invoice-parser-backend -n invoice-parser

# Access service
kubectl port-forward svc/invoice-parser-backend 8000:80 -n invoice-parser
```

---

## Cloud Deployment Options

### AWS EC2 + RDS

```bash
# 1. Launch EC2 instance (Ubuntu 22.04)
# Instance type: t3.small or larger
# Security group: Allow 8000, 80, 443

# 2. SSH into instance
ssh -i key.pem ec2-user@instance-ip

# 3. Install dependencies
sudo apt-get update
sudo apt-get install -y python3.11 python3-pip git redis-server

# 4. Clone repository
git clone https://github.com/yourname/invoice-parser.git
cd invoice-parser/backend

# 5. Setup
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 6. Configure
cp .env.example .env
nano .env  # Add MISTRAL_API_KEY and RDS endpoint

# 7. Start service
nohup python main.py > logs/app.log 2>&1 &

# 8. Setup nginx reverse proxy
sudo apt-get install -y nginx
# Create /etc/nginx/sites-available/invoice-parser:
server {
    listen 80;
    server_name your-domain.com;
    
    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}

# 9. Enable and start nginx
sudo systemctl enable nginx
sudo systemctl start nginx

# 10. SSL certificate (Let's Encrypt)
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

### Google Cloud Run

```bash
# 1. Enable Cloud Run API
gcloud services enable run

# 2. Build and push to Container Registry
gcloud builds submit --tag gcr.io/PROJECT_ID/invoice-parser-backend

# 3. Deploy
gcloud run deploy invoice-parser-backend \
  --image gcr.io/PROJECT_ID/invoice-parser-backend \
  --platform managed \
  --region us-central1 \
  --memory 512Mi \
  --timeout 3600 \
  --set-env-vars MISTRAL_API_KEY=sk_your_key,REDIS_ENABLED=true \
  --allow-unauthenticated

# 4. Get URL
gcloud run services describe invoice-parser-backend --region us-central1
```

### Azure App Service

```bash
# 1. Create resource group
az group create --name invoice-parser --location eastus

# 2. Create App Service plan
az appservice plan create \
  --name invoice-parser-plan \
  --resource-group invoice-parser \
  --sku B1 --is-linux

# 3. Create Web App
az webapp create \
  --resource-group invoice-parser \
  --plan invoice-parser-plan \
  --name invoice-parser-backend \
  --runtime "PYTHON|3.11"

# 4. Deploy from GitHub
az webapp deployment github-actions add \
  --repo-url https://github.com/yourname/invoice-parser \
  --branch main

# 5. Configure environment variables
az webapp config appsettings set \
  --resource-group invoice-parser \
  --name invoice-parser-backend \
  --settings MISTRAL_API_KEY=sk_your_key REDIS_ENABLED=true
```

---

## Production Monitoring

### Health Checks

```bash
# Monitor endpoint availability
watch -n 5 'curl -s http://your-domain.com/health | jq .'

# Monitor logs in real-time
tail -f logs/app.log

# Check disk usage
watch -n 5 'du -sh /tmp/invoice_uploads'

# Monitor memory
watch -n 5 'free -h'
```

### Logging to External Services

**CloudWatch (AWS):**
```python
import watchtower
import logging

logger = logging.getLogger("invoice_parser")
logger.addHandler(watchtower.CloudWatchLogHandler())
```

**Datadog:**
```python
from datadog import initialize, api
from ddtrace import tracer

initialize()

@tracer.wrap()
async def process_invoice():
    # Code here is traced
    pass
```

---

## Security Checklist

- [ ] MISTRAL_API_KEY not in version control
- [ ] DEBUG=false in production
- [ ] CORS_ORIGINS restricted to your domain
- [ ] HTTPS/SSL enabled
- [ ] Rate limiting implemented
- [ ] Input validation enabled
- [ ] No sensitive data in logs
- [ ] Regular backups of results
- [ ] Redis password protected
- [ ] Firewall rules configured

---

## Performance Optimization

### Gunicorn (Production ASGI Server)

```bash
pip install gunicorn

# Run with multiple workers
gunicorn -w 4 main:app --bind 0.0.0.0:8000 --timeout 120
```

### Nginx Reverse Proxy

```nginx
upstream invoice_parser {
    server localhost:8000;
}

server {
    listen 80;
    server_name your-domain.com;
    
    # Compression
    gzip on;
    gzip_types application/json;
    
    # Caching
    proxy_cache_path /var/cache/nginx levels=1:2 keys_zone=api_cache:10m;
    
    location / {
        proxy_pass http://invoice_parser;
        proxy_cache api_cache;
        proxy_cache_valid 200 1m;
        
        # Timeouts
        proxy_connect_timeout 60s;
        proxy_send_timeout 120s;
        proxy_read_timeout 120s;
    }
}
```

### Database Connection Pooling

```python
# If adding database, use connection pool
from sqlalchemy import create_engine
from sqlalchemy.pool import QueuePool

engine = create_engine(
    DATABASE_URL,
    poolclass=QueuePool,
    pool_size=10,
    max_overflow=20
)
```

---

## Scaling Considerations

### Horizontal Scaling

1. **Load Balancer**: AWS ELB, nginx, HAProxy
2. **Session Storage**: Redis (shared across instances)
3. **File Storage**: S3, GCS, or NFS
4. **Database**: RDS, Cloud SQL, or managed database

### Vertical Scaling

1. Increase instance size
2. Increase memory allocation
3. Increase CPU cores
4. Optimize queries and processing

---

## Rollback Plan

```bash
# If deployment fails:

# 1. Check logs
docker-compose logs backend

# 2. Rollback to previous version
docker-compose down
docker pull invoice-parser-backend:previous-tag
docker-compose up -d

# 3. Verify health
curl http://localhost:8000/health

# 4. Check sessions persisted (in Redis)
# Data should be intact
```

---

## Post-Deployment Tests

```bash
#!/bin/bash

DOMAIN="https://your-domain.com"

# 1. Health check
echo "Testing health..."
curl -s $DOMAIN/health | jq .

# 2. Create session
echo "Creating session..."
BATCH=$(curl -s -X POST $DOMAIN/api/sessions | jq -r '.batch_id')
echo "Batch: $BATCH"

# 3. Upload test file
echo "Uploading test file..."
curl -X POST $DOMAIN/api/sessions/$BATCH/upload \
  -F "files=@test_invoice.pdf"

# 4. Check status
echo "Checking status..."
curl -s $DOMAIN/api/sessions/$BATCH/status | jq '.'

# 5. Verify results
echo "Verifying results..."
# Check logs, data storage, etc.

echo "✅ All tests passed!"
```

---

## Maintenance

### Scheduled Tasks

```bash
# Daily backup
0 2 * * * /usr/local/bin/backup_results.sh

# Weekly cleanup
0 3 * * 0 /usr/local/bin/cleanup_old_batches.sh

# Log rotation
0 0 * * * /usr/local/bin/rotate_logs.sh
```

### Updates

```bash
# Update dependencies
pip list --outdated
pip install --upgrade package-name

# Rebuild Docker image
docker build -t invoice-parser-backend:latest .

# Push to registry
docker push invoice-parser-backend:latest

# Redeploy
docker-compose up -d --pull always backend
```

---

## Troubleshooting Production Issues

### High CPU Usage
1. Check logs for errors
2. Monitor active requests
3. Scale horizontally
4. Optimize Mistral API calls

### Memory Leaks
1. Monitor memory over time
2. Check for unclosed file handles
3. Review async task cleanup
4. Restart service if needed

### Database Connection Issues
1. Check Redis connectivity
2. Verify connection pooling
3. Check firewall rules
4. Monitor connection pool stats

### Slow Response Times
1. Check backend logs
2. Monitor API response times
3. Check file sizes
4. Review Mistral API latency

---

## Emergency Procedures

### Service Down
```bash
# 1. Check service status
systemctl status invoice-parser

# 2. View recent logs
journalctl -u invoice-parser -n 50

# 3. Restart service
systemctl restart invoice-parser

# 4. Check if Redis is running
redis-cli ping

# 5. If all else fails, rollback
git revert <commit>
make restart
```

### Data Corruption
```bash
# 1. Stop service
systemctl stop invoice-parser

# 2. Backup current state
cp -r /tmp/invoice_uploads /tmp/invoice_uploads.backup

# 3. Clear Redis cache
redis-cli FLUSHALL

# 4. Restart
systemctl start invoice-parser
```

---

## Compliance & Security

- [ ] GDPR compliant (data deletion after session expires)
- [ ] No PII stored permanently
- [ ] Audit logs maintained
- [ ] Encryption in transit (HTTPS)
- [ ] Encryption at rest (if using persistent storage)
- [ ] Regular security scans
- [ ] Dependency updates scheduled
- [ ] Penetration testing completed

---

## Success Criteria

- ✅ Health check returns 200 within 2s
- ✅ Session creation within 500ms
- ✅ File upload returns 202 within 1s
- ✅ Status polling within 500ms
- ✅ Invoice processing within 2s per file
- ✅ Uptime >99.5%
- ✅ Error rate <0.1%
- ✅ No data loss
- ✅ Logs accessible and searchable
- ✅ Alerts configured for critical issues

---

Ready to deploy! 🚀
