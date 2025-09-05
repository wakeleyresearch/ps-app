# gunicorn.conf.py

import multiprocessing
import os

# Server socket
bind = f"0.0.0.0:{os.environ.get('PORT', 5000)}"
backlog = 2048

# Worker processes
workers = max(1, min(multiprocessing.cpu_count(), 4))  # Limit to 4 workers max
worker_class = "sync"  # Use sync workers for I/O bound tasks
worker_connections = 1000
max_requests = 1000  # Restart workers after 1000 requests to prevent memory leaks
max_requests_jitter = 50  # Add randomness to max_requests

# Timeout settings - CRITICAL for preventing worker timeouts
timeout = 30  # Reduced from 120 to 30 seconds for faster failover
keepalive = 2
graceful_timeout = 30  # Time to wait for workers to finish current requests during shutdown

# Logging
errorlog = "-"  # stderr
loglevel = "info"
accesslog = "-"  # stdout
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = "pokestop-app"

# Security
limit_request_line = 4094
limit_request_fields = 100
limit_request_field_size = 8190

# Preload application for better memory usage
preload_app = True

# Worker management
worker_tmp_dir = "/dev/shm"  # Use memory for worker temp files if available

# Enable stats for monitoring
statsd_host = None  # Set if you have StatsD monitoring

def when_ready(server):
    """Called when the server is ready to accept connections."""
    server.log.info("Pokestop app server ready")

def worker_int(worker):
    """Called when a worker receives the INT or QUIT signal."""
    worker.log.info("Worker received INT or QUIT signal")

def pre_fork(server, worker):
    """Called before a worker is forked."""
    server.log.info(f"Worker spawned (pid: {worker.pid})")

def post_fork(server, worker):
    """Called after a worker is forked."""
    server.log.info(f"Worker spawned (pid: {worker.pid})")

def pre_exec(server):
    """Called before a new master process is forked."""
    server.log.info("Forked child, re-executing.")

def worker_abort(worker):
    """Called when a worker process is killed by a timeout."""
    worker.log.info(f"Worker timeout (pid: {worker.pid})")
