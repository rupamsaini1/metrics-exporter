# Metrics Exporter

A custom Prometheus metrics exporter written in Python for Linux hosts, Docker environments, Docker Compose services, and optional PostgreSQL monitoring.

The exporter collects host system metrics, Docker container metrics, Docker Compose container status, PostgreSQL database metrics, and top host processes. Metrics are exposed in Prometheus format for Grafana dashboards and alerting.

## Features

- Host CPU, RAM, swap, disk, network, and block I/O metrics
- System CPU I/O wait percentage
- Top host processes by CPU and memory usage
- Docker container CPU, memory, network, and block I/O metrics
- Docker Compose container status with Compose project labels
- Optional PostgreSQL availability, connection, transaction, cache, tuple, and database size metrics
- Configurable collection intervals and log level
- Prometheus-compatible `/metrics` endpoint

## Tech Stack

- Python 3.11
- Prometheus Client
- Docker SDK for Python
- psutil
- psycopg2
- Docker / Docker Compose
- Grafana / Prometheus

## Metrics Endpoint

```text
http://<host>:8000/metrics
```

## Metrics Exposed

### System Metrics

- `server_status`
- `system_cpu_usage_percent`
- `system_io_wait_percent`
- `system_ram_usage_bytes`
- `system_ram_total_bytes`
- `system_ram_usage_percent`
- `system_swap_usage_bytes`
- `system_swap_total_bytes`
- `system_swap_usage_percent`
- `system_disk_usage_bytes{mountpoint}`
- `system_disk_total_bytes{mountpoint}`
- `system_disk_usage_percent{mountpoint}`
- `system_network_receive_bytes_total{interface}`
- `system_network_transmit_bytes_total{interface}`
- `system_network_receive_bytes_per_second{interface}`
- `system_network_transmit_bytes_per_second{interface}`
- `system_network_receive_packets_per_second{interface}`
- `system_block_read_bytes_total{device}`
- `system_block_write_bytes_total{device}`
- `system_block_read_bytes_per_second{device}`
- `system_block_write_bytes_per_second{device}`
- `system_block_read_ops_total{device}`
- `system_block_write_ops_total{device}`

### Docker Metrics

- `container_cpu_usage_percent{container_name, project}`
- `container_memory_usage_bytes{container_name, project}`
- `container_memory_limit_bytes{container_name, project}`
- `container_network_receive_bytes_total{container_name, project, interface}`
- `container_network_transmit_bytes_total{container_name, project, interface}`
- `container_network_receive_bytes_per_second{container_name, project, interface}`
- `container_network_transmit_bytes_per_second{container_name, project, interface}`
- `container_network_receive_packets_per_second{container_name, project, interface}`
- `container_block_read_bytes_total{container_name, project}`
- `container_block_write_bytes_total{container_name, project}`
- `container_block_read_bytes_per_second{container_name, project}`
- `container_block_write_bytes_per_second{container_name, project}`

### Docker Compose Status

- `docker_compose_container_status{container_name, project}`
- `docker_compose_container_status_flat{name}`

### PostgreSQL Metrics

- `postgres_up{database}`
- `postgres_connections{database}`
- `postgres_max_connections{database}`
- `postgres_xact_commit_total{database}`
- `postgres_xact_rollback_total{database}`
- `postgres_blks_read_total{database}`
- `postgres_blks_hit_total{database}`
- `postgres_tup_returned_total{database}`
- `postgres_tup_fetched_total{database}`
- `postgres_tup_inserted_total{database}`
- `postgres_tup_updated_total{database}`
- `postgres_tup_deleted_total{database}`
- `postgres_database_size_bytes{database}`
- `postgres_active_connections{database, state}`

### Top Processes

- `top_process_cpu_usage_percent{pid, name}`
- `top_process_memory_usage_bytes{pid, name}`

## Environment Variables

| Variable | Default | Description |
| --- | --- | --- |
| `METRICS_PORT` | `8000` | Metrics HTTP port. |
| `COLLECTION_INTERVAL` | `5` | Host metric collection interval in seconds. |
| `DOCKER_COLLECTION_INTERVAL` | `30` | Docker stats collection interval in seconds. |
| `LOG_LEVEL` | `INFO` | Python logging level. |
| `SUMMARY_LOG_EVERY` | `12` | Collection cycles between summary log messages. |
| `POSTGRES_URL` | unset | PostgreSQL connection string. Takes precedence over individual PostgreSQL settings. |
| `POSTGRES_HOST` | unset | PostgreSQL host. Enables PostgreSQL metrics when `POSTGRES_URL` is not set. |
| `POSTGRES_PORT` | `5432` | PostgreSQL port. |
| `POSTGRES_DB` | `postgres` | PostgreSQL database used for the metrics connection. |
| `POSTGRES_USER` | unset | PostgreSQL username. |
| `POSTGRES_PASSWORD` | unset | PostgreSQL password. |
| `POSTGRES_SSLMODE` | `prefer` | PostgreSQL SSL mode. |

PostgreSQL metrics are disabled automatically when neither `POSTGRES_URL` nor `POSTGRES_HOST` is configured.

## Run With Docker Compose

```bash
cd metrics-exporter
docker compose up -d --build
```

The compose file exposes the exporter on port `8000` and mounts Docker plus host proc/sys paths so the exporter can read host and container metrics.

To enable PostgreSQL metrics, add PostgreSQL configuration to the `metrics-collector` service:

```yaml
environment:
  - POSTGRES_HOST=postgres
  - POSTGRES_PORT=5432
  - POSTGRES_DB=postgres
  - POSTGRES_USER=postgres
  - POSTGRES_PASSWORD=postgres
```

You can also use a connection string:

```yaml
environment:
  - POSTGRES_URL=postgresql://postgres:postgres@postgres:5432/postgres
```

## Local Run

```bash
cd metrics-exporter
pip install -r requirements.txt
python metrics_collector.py
```

## Prometheus Scrape Config

```yaml
scrape_configs:
  - job_name: metrics-exporter
    static_configs:
      - targets:
          - <host-ip>:8000
```
