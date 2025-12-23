# Metrics Exporter 🚀

A **custom Prometheus metrics exporter** written in Python for monitoring
**Linux host systems and Docker environments**.

This exporter collects **system metrics, Docker container metrics,
Docker Compose container status, and top host processes**, and exposes
them in Prometheus format for **Grafana dashboards and alerting**.

> ⚠️ **Note:**  
> This project was **generated with the assistance of AI** and then reviewed,
> tested, and structured by the author for learning and demonstration purposes.
> The design decisions, deployment strategy, and documentation reflect
> real-world DevOps practices.

---

## ✨ Features

### 🔹 System Metrics
- CPU usage (host-level)
- RAM usage (used, total, percentage)
- Swap usage
- Disk usage per mountpoint
- Top CPU & memory consuming host processes

### 🔹 Docker Metrics
- Container CPU usage
- Container memory usage & limits
- Docker Compose container status (Up / Down)
- Project-level labeling using Compose metadata

### 🔹 Production-Ready Design
- Reads **host `/proc`** when mounted
- Graceful Docker connectivity handling
- Optimized collection intervals
- Threaded container metric collection
- Prometheus-compatible `/metrics` endpoint

---

## 🧰 Tech Stack

- Python 3
- Prometheus Client
- Docker SDK
- psutil
- Docker / Docker Compose
- Grafana

---

## 🏗 Architecture Overview

Linux Host
├─ System Metrics (CPU, RAM, Disk, Processes)
├─ Docker Daemon
│ ├─ Containers
│ └─ Compose Projects
│
└─ Metrics Exporter
↓
Prometheus
↓
Grafana

## 🚀 Metrics Endpoint
http://<host>:8000/metrics

## 📦 Metrics Exposed

### 🔹 System Metrics
- system_cpu_usage_percent
- system_ram_usage_bytes
- system_ram_total_bytes
- system_ram_usage_percent
- system_swap_usage_bytes
- system_swap_total_bytes
- system_swap_usage_percent
- system_disk_usage_bytes{mountpoint}
- system_disk_total_bytes{mountpoint}
- system_disk_usage_percent{mountpoint}

### 🔹 Docker Metrics
- container_cpu_usage_percent{container_name, project}
- container_memory_usage_bytes{container_name, project}
- container_memory_limit_bytes{container_name, project}

### 🔹 Docker Compose Status
- docker_compose_container_status{container_name, project}
- docker_compose_container_status_flat{name}

### 🔹 Top Processes
- top_process_cpu_usage_percent{pid, name}
- top_process_memory_usage_bytes{pid, name}

---

## 🛠 Environment Variables

| Variable | Default | Description |
|--------|--------|-------------|
| METRICS_PORT | 8000 | Metrics HTTP port |
| COLLECTION_INTERVAL | 5 | System metrics interval (seconds) |
| DOCKER_COLLECTION_INTERVAL | 30 | Docker stats interval (seconds) |

---

## 🐳 Run with Docker Compose (Recommended)
docker-compose up -d
