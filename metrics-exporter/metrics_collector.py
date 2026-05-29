#!/usr/bin/env python3
"""
System and Docker Metrics Collector for Prometheus
Collects CPU, RAM, Swap, Storage, Docker container metrics, and top host processes
Also collects Docker Compose container status (Up/Down)
"""

import os
import time
import psutil
import docker
from prometheus_client import Gauge, start_http_server
import logging
from concurrent.futures import ThreadPoolExecutor

# Configure logging
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, log_level, logging.INFO), format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Use host /proc if mounted
if os.path.exists("/host/proc"):
    logger.info("Using host /proc for psutil (reading host processes)")
    psutil.PROCFS_PATH = "/host/proc"
else:
    logger.info("Using container's /proc for psutil (container processes only)")

class MetricsCollector:
    def __init__(self):
        self.collection_runs = 0
        self.summary_log_every = max(1, int(os.getenv("SUMMARY_LOG_EVERY", 12)))

        # Initialize Docker client
        try:
            self.docker_client = None
            try:
                self.docker_client = docker.DockerClient(base_url='unix://var/run/docker.sock', version='auto')
                self.docker_client.ping()
                logger.info("Docker client initialized successfully")
            except Exception as e:
                logger.warning(f"Explicit socket Docker connection failed: {e}")
                try:
                    from docker import APIClient
                    api_client = APIClient(base_url='unix://var/run/docker.sock')
                    api_client.ping()
                    self.docker_client = docker.DockerClient(base_url='unix://var/run/docker.sock')
                    logger.info("Docker client initialized successfully with API client test")
                except Exception as e2:
                    logger.error(f"All Docker connection methods failed: {e2}")
                    self.docker_client = None
        except Exception as e:
            logger.error(f"Unexpected error initializing Docker client: {e}")
            self.docker_client = None

        # System Metrics
        self.cpu_usage = Gauge('system_cpu_usage_percent', 'Average CPU usage across all cores')
        self.ram_usage = Gauge('system_ram_usage_bytes', 'RAM usage in bytes')
        self.ram_total = Gauge('system_ram_total_bytes', 'Total RAM in bytes')
        self.ram_usage_percent = Gauge('system_ram_usage_percent', 'RAM usage percentage')

        self.swap_usage = Gauge('system_swap_usage_bytes', 'Swap usage in bytes')
        self.swap_total = Gauge('system_swap_total_bytes', 'Total swap in bytes')
        self.swap_usage_percent = Gauge('system_swap_usage_percent', 'Swap usage percentage')

        self.disk_usage = Gauge('system_disk_usage_bytes', 'Disk usage in bytes', ['mountpoint'])
        self.disk_total = Gauge('system_disk_total_bytes', 'Total disk space in bytes', ['mountpoint'])
        self.disk_usage_percent = Gauge('system_disk_usage_percent', 'Disk usage percentage', ['mountpoint'])
        self.server_status = Gauge('server_status', 'Server status for dashboards: 1=up, 0=down')
        self.system_io_wait_percent = Gauge(
            'system_io_wait_percent',
            'System-wide CPU I/O wait percentage'
        )

        self.system_network_receive_bytes = Gauge(
            'system_network_receive_bytes_total',
            'Total bytes received by the host network interface',
            ['interface']
        )
        self.system_network_transmit_bytes = Gauge(
            'system_network_transmit_bytes_total',
            'Total bytes transmitted by the host network interface',
            ['interface']
        )
        self.system_network_receive_bytes_rate = Gauge(
            'system_network_receive_bytes_per_second',
            'Bytes received per second by the host network interface',
            ['interface']
        )
        self.system_network_transmit_bytes_rate = Gauge(
            'system_network_transmit_bytes_per_second',
            'Bytes transmitted per second by the host network interface',
            ['interface']
        )
        self.system_network_receive_packets_rate = Gauge(
            'system_network_receive_packets_per_second',
            'Inbound packets per second by the host network interface',
            ['interface']
        )

        self.system_block_read_bytes = Gauge(
            'system_block_read_bytes_total',
            'Total bytes read by the host block device',
            ['device']
        )
        self.system_block_write_bytes = Gauge(
            'system_block_write_bytes_total',
            'Total bytes written by the host block device',
            ['device']
        )
        self.system_block_read_bytes_rate = Gauge(
            'system_block_read_bytes_per_second',
            'Read throughput per second for the host block device',
            ['device']
        )
        self.system_block_write_bytes_rate = Gauge(
            'system_block_write_bytes_per_second',
            'Write throughput per second for the host block device',
            ['device']
        )
        self.system_block_read_ops = Gauge(
            'system_block_read_ops_total',
            'Total read operations by the host block device',
            ['device']
        )
        self.system_block_write_ops = Gauge(
            'system_block_write_ops_total',
            'Total write operations by the host block device',
            ['device']
        )

        self.postgres_up = Gauge('postgres_up', 'PostgreSQL availability status: 1=up, 0=down', ['database'])
        self.postgres_connections = Gauge('postgres_connections', 'Number of active PostgreSQL connections', ['database'])
        self.postgres_max_connections = Gauge('postgres_max_connections', 'PostgreSQL max connections setting', ['database'])
        self.postgres_xact_commit = Gauge('postgres_xact_commit_total', 'Total number of committed transactions', ['database'])
        self.postgres_xact_rollback = Gauge('postgres_xact_rollback_total', 'Total number of rolled back transactions', ['database'])
        self.postgres_blks_read = Gauge('postgres_blks_read_total', 'Total disk blocks read from PostgreSQL', ['database'])
        self.postgres_blks_hit = Gauge('postgres_blks_hit_total', 'Total disk blocks found in PostgreSQL cache', ['database'])
        self.postgres_tup_returned = Gauge('postgres_tup_returned_total', 'Total tuples returned by PostgreSQL', ['database'])
        self.postgres_tup_fetched = Gauge('postgres_tup_fetched_total', 'Total tuples fetched by PostgreSQL', ['database'])
        self.postgres_tup_inserted = Gauge('postgres_tup_inserted_total', 'Total tuples inserted into PostgreSQL', ['database'])
        self.postgres_tup_updated = Gauge('postgres_tup_updated_total', 'Total tuples updated in PostgreSQL', ['database'])
        self.postgres_tup_deleted = Gauge('postgres_tup_deleted_total', 'Total tuples deleted from PostgreSQL', ['database'])
        self.postgres_database_size = Gauge('postgres_database_size_bytes', 'PostgreSQL database size in bytes', ['database'])
        self.postgres_connection_state = Gauge('postgres_active_connections', 'Number of active PostgreSQL connections grouped by state', ['database', 'state'])

        self.postgres_metrics_enabled = False
        self.postgres_conn_info = None
        self._init_postgres()

        self.container_cpu_usage = Gauge('container_cpu_usage_percent', 'Container CPU usage percentage', ['container_name', 'project'])
        self.container_memory_usage = Gauge('container_memory_usage_bytes', 'Container memory usage in bytes', ['container_name', 'project'])
        self.container_memory_limit = Gauge('container_memory_limit_bytes', 'Container memory limit in bytes', ['container_name', 'project'])
        self.container_network_receive_bytes = Gauge(
            'container_network_receive_bytes_total',
            'Total bytes received by the container network interface',
            ['container_name', 'project', 'interface']
        )
        self.container_network_transmit_bytes = Gauge(
            'container_network_transmit_bytes_total',
            'Total bytes transmitted by the container network interface',
            ['container_name', 'project', 'interface']
        )
        self.container_network_receive_bytes_rate = Gauge(
            'container_network_receive_bytes_per_second',
            'Bytes received per second by the container network interface',
            ['container_name', 'project', 'interface']
        )
        self.container_network_transmit_bytes_rate = Gauge(
            'container_network_transmit_bytes_per_second',
            'Bytes transmitted per second by the container network interface',
            ['container_name', 'project', 'interface']
        )
        self.container_network_receive_packets_rate = Gauge(
            'container_network_receive_packets_per_second',
            'Inbound packets per second for the container network interface',
            ['container_name', 'project', 'interface']
        )
        self.container_block_read_bytes = Gauge(
            'container_block_read_bytes_total',
            'Total block bytes read by the container',
            ['container_name', 'project']
        )
        self.container_block_write_bytes = Gauge(
            'container_block_write_bytes_total',
            'Total block bytes written by the container',
            ['container_name', 'project']
        )
        self.container_block_read_bytes_rate = Gauge(
            'container_block_read_bytes_per_second',
            'Block read throughput per second for the container',
            ['container_name', 'project']
        )
        self.container_block_write_bytes_rate = Gauge(
            'container_block_write_bytes_per_second',
            'Block write throughput per second for the container',
            ['container_name', 'project']
        )

        # Docker Compose container status
        self.compose_container_status = Gauge(
            'docker_compose_container_status',
            'Docker Compose container status: 1=running, 0=stopped',
            ['container_name', 'project']
        )
        self.compose_container_status_flat = Gauge(
            'docker_compose_container_status_flat',
            'Container status for Grafana status panel (1=running, 0=stopped)',
            ['name']
        )

        # Top processes metrics
        self.top_cpu_processes = Gauge('top_process_cpu_usage_percent', 'Top 5 processes by CPU usage', ['pid', 'name'])
        self.top_memory_processes = Gauge('top_process_memory_usage_bytes', 'Top 5 processes by Memory usage', ['pid', 'name'])

        self._previous_host_network = {}
        self._previous_host_block = {}
        self._previous_container_network = {}
        self._previous_container_block = {}

        logger.info(
            "Metrics collector initialized (docker_enabled=%s, summary_log_every=%s)",
            self.docker_client is not None,
            self.summary_log_every,
        )

    def _calculate_rate(self, previous_total, current_total, elapsed_seconds):
        if elapsed_seconds <= 0:
            return 0.0
        delta = current_total - previous_total
        if delta < 0:
            return 0.0
        return delta / elapsed_seconds

    def test_docker_connectivity(self):
        if not self.docker_client:
            return False, "Docker client not initialized"
        try:
            version_info = self.docker_client.version()
            containers = self.docker_client.containers.list()
            return True, f"Connected to Docker {version_info.get('Version', 'unknown')}, found {len(containers)} containers"
        except Exception as e:
            return False, f"Docker connectivity test failed: {e}"

    def _init_postgres(self):
        postgres_url = os.getenv('POSTGRES_URL')
        postgres_db = os.getenv('POSTGRES_DB', 'postgres')
        postgres_host = os.getenv('POSTGRES_HOST')

        if postgres_url:
            self.postgres_conn_info = postgres_url
        elif postgres_host:
            self.postgres_conn_info = {
                'dbname': postgres_db,
                'host': postgres_host,
                'port': int(os.getenv('POSTGRES_PORT', 5432)),
                'user': os.getenv('POSTGRES_USER'),
                'password': os.getenv('POSTGRES_PASSWORD'),
                'sslmode': os.getenv('POSTGRES_SSLMODE', 'prefer'),
            }
        else:
            logger.info("PostgreSQL metrics disabled because no POSTGRES_URL or POSTGRES_HOST configuration was provided")
            return

        try:
            import psycopg2
            self.psycopg2 = psycopg2
            self.postgres_metrics_enabled = True
            connected, status = self.test_postgres_connectivity()
            logger.info("PostgreSQL metrics enabled=%s (%s)", connected, status)
        except ImportError:
            logger.error("psycopg2-binary is required for PostgreSQL metrics but is not installed")
            self.postgres_metrics_enabled = False
        except Exception as e:
            logger.error(f"Unable to initialize PostgreSQL metrics: {e}")
            self.postgres_metrics_enabled = False

    def _get_postgres_connection(self):
        if not self.postgres_conn_info:
            raise RuntimeError("PostgreSQL connection info not configured")
        if isinstance(self.postgres_conn_info, str):
            return self.psycopg2.connect(self.postgres_conn_info)
        return self.psycopg2.connect(**self.postgres_conn_info)

    def test_postgres_connectivity(self):
        if not self.postgres_metrics_enabled:
            return False, "PostgreSQL metrics disabled"
        try:
            with self._get_postgres_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute('SELECT 1')
                    cur.fetchone()
            return True, "PostgreSQL connectivity test passed"
        except Exception as e:
            return False, f"PostgreSQL connectivity test failed: {e}"

    def collect_postgres_metrics(self):
        if not self.postgres_metrics_enabled:
            return
        try:
            with self._get_postgres_connection() as conn:
                with conn.cursor() as cur:
                    # Collect metrics for every database in the cluster
                    cur.execute(
                        """
                        SELECT datname,
                               numbackends,
                               xact_commit,
                               xact_rollback,
                               blks_read,
                               blks_hit,
                               tup_returned,
                               tup_fetched,
                               tup_inserted,
                               tup_updated,
                               tup_deleted
                        FROM pg_stat_database
                        WHERE datname NOT IN ('template0', 'template1')
                        """
                    )
                    for row in cur.fetchall():
                        (db_name, numbackends, xact_commit, xact_rollback, blks_read, blks_hit,
                         tup_returned, tup_fetched, tup_inserted, tup_updated, tup_deleted) = row

                        self.postgres_up.labels(database=db_name).set(1)
                        self.postgres_connections.labels(database=db_name).set(numbackends)
                        self.postgres_xact_commit.labels(database=db_name).set(xact_commit)
                        self.postgres_xact_rollback.labels(database=db_name).set(xact_rollback)
                        self.postgres_blks_read.labels(database=db_name).set(blks_read)
                        self.postgres_blks_hit.labels(database=db_name).set(blks_hit)
                        self.postgres_tup_returned.labels(database=db_name).set(tup_returned)
                        self.postgres_tup_fetched.labels(database=db_name).set(tup_fetched)
                        self.postgres_tup_inserted.labels(database=db_name).set(tup_inserted)
                        self.postgres_tup_updated.labels(database=db_name).set(tup_updated)
                        self.postgres_tup_deleted.labels(database=db_name).set(tup_deleted)

                    # Cluster-level max connections
                    cur.execute('SELECT setting::int FROM pg_settings WHERE name = %s', ('max_connections',))
                    max_conn = cur.fetchone()
                    if max_conn:
                        self.postgres_max_connections.labels(database='cluster').set(max_conn[0])

                    # Database size for every database
                    cur.execute(
                        "SELECT datname, pg_database_size(datname) FROM pg_database WHERE datname NOT IN ('template0', 'template1')"
                    )
                    for db_name, size in cur.fetchall():
                        self.postgres_database_size.labels(database=db_name).set(size)

                    # Connection state counts across the whole cluster
                    cur.execute(
                        "SELECT datname, state, count(*) FROM pg_stat_activity GROUP BY datname, state"
                    )
                    for db_name, state, count in cur.fetchall():
                        self.postgres_connection_state.labels(database=db_name, state=state or 'unknown').set(count)
        except Exception as e:
            logger.warning(f"Error collecting PostgreSQL metrics: {e}")

    def collect_system_cpu(self):
        try:
            if os.path.exists("/host/proc/stat"):  # Host /proc is mounted
                with open("/host/proc/stat", "r") as f:
                    cpu_line = f.readline().split()[1:]
                    cpu_times = list(map(int, cpu_line))

                idle_time = cpu_times[3]
                total_time = sum(cpu_times)

                if not hasattr(self, "_last_total"):
                    self._last_total = total_time
                    self._last_idle = idle_time
                    return

                total_diff = total_time - self._last_total
                idle_diff = idle_time - self._last_idle

                cpu_percent = (1 - idle_diff / total_diff) * 100.0
                self.cpu_usage.set(cpu_percent)

                self._last_total = total_time
                self._last_idle = idle_time
            else:
                cpu_percent = psutil.cpu_percent(interval=None)
                self.cpu_usage.set(cpu_percent)

        except Exception as e:
            logger.error(f"Error collecting CPU metrics: {e}")

    def collect_system_io_wait(self):
        try:
            proc_path = "/host/proc/stat" if os.path.exists("/host/proc/stat") else "/proc/stat"
            with open(proc_path, "r") as f:
                cpu_line = f.readline().split()[1:]
                cpu_times = list(map(int, cpu_line))

            # Linux cpu fields begin with: user nice system idle iowait ...
            iowait_time = cpu_times[4] if len(cpu_times) > 4 else 0
            total_time = sum(cpu_times)

            if not hasattr(self, "_last_iowait_total"):
                self._last_iowait_total = iowait_time
                self._last_iowait_cpu_total = total_time
                self.system_io_wait_percent.set(0)
                return

            total_diff = total_time - self._last_iowait_cpu_total
            iowait_diff = iowait_time - self._last_iowait_total

            if total_diff <= 0 or iowait_diff < 0:
                self.system_io_wait_percent.set(0)
            else:
                self.system_io_wait_percent.set((iowait_diff / total_diff) * 100.0)

            self._last_iowait_total = iowait_time
            self._last_iowait_cpu_total = total_time
        except Exception as e:
            logger.error(f"Error collecting system io wait metrics: {e}")

    def collect_system_memory(self):
        try:
            memory = psutil.virtual_memory()
            self.ram_usage.set(memory.used)
            self.ram_total.set(memory.total)
            self.ram_usage_percent.set(memory.percent)
        except Exception as e:
            logger.error(f"Error collecting memory metrics: {e}")

    def collect_system_swap(self):
        try:
            swap = psutil.swap_memory()
            self.swap_usage.set(swap.used)
            self.swap_total.set(swap.total)
            self.swap_usage_percent.set(swap.percent)
        except Exception as e:
            logger.error(f"Error collecting swap metrics: {e}")

    def collect_disk_usage(self):
        try:
            for partition in psutil.disk_partitions():
                try:
                    if partition.mountpoint.startswith(('/proc', '/sys', '/dev', '/run')):
                        continue
                    usage = psutil.disk_usage(partition.mountpoint)
                    self.disk_usage.labels(mountpoint=partition.mountpoint).set(usage.used)
                    self.disk_total.labels(mountpoint=partition.mountpoint).set(usage.total)
                    self.disk_usage_percent.labels(mountpoint=partition.mountpoint).set(usage.percent)
                except PermissionError:
                    continue
                except Exception as e:
                    logger.warning(f"Error collecting disk metrics for {partition.mountpoint}: {e}")
        except Exception as e:
            logger.error(f"Error collecting disk metrics: {e}")

    def collect_server_status(self):
        try:
            self.server_status.set(1)
        except Exception as e:
            logger.error(f"Error collecting server status: {e}")

    def collect_system_network_io(self):
        try:
            now = time.time()
            counters = psutil.net_io_counters(pernic=True)
            for interface, stats in counters.items():
                if interface == 'lo':
                    continue

                self.system_network_receive_bytes.labels(interface=interface).set(stats.bytes_recv)
                self.system_network_transmit_bytes.labels(interface=interface).set(stats.bytes_sent)

                previous = self._previous_host_network.get(interface)
                if previous:
                    elapsed = now - previous['timestamp']
                    self.system_network_receive_bytes_rate.labels(interface=interface).set(
                        self._calculate_rate(previous['bytes_recv'], stats.bytes_recv, elapsed)
                    )
                    self.system_network_transmit_bytes_rate.labels(interface=interface).set(
                        self._calculate_rate(previous['bytes_sent'], stats.bytes_sent, elapsed)
                    )
                    self.system_network_receive_packets_rate.labels(interface=interface).set(
                        self._calculate_rate(previous['packets_recv'], stats.packets_recv, elapsed)
                    )
                else:
                    self.system_network_receive_bytes_rate.labels(interface=interface).set(0)
                    self.system_network_transmit_bytes_rate.labels(interface=interface).set(0)
                    self.system_network_receive_packets_rate.labels(interface=interface).set(0)

                self._previous_host_network[interface] = {
                    'timestamp': now,
                    'bytes_recv': stats.bytes_recv,
                    'bytes_sent': stats.bytes_sent,
                    'packets_recv': stats.packets_recv,
                }
        except Exception as e:
            logger.error(f"Error collecting system network metrics: {e}")

    def collect_system_block_io(self):
        try:
            now = time.time()
            counters = psutil.disk_io_counters(perdisk=True) or {}
            for device, stats in counters.items():
                self.system_block_read_bytes.labels(device=device).set(stats.read_bytes)
                self.system_block_write_bytes.labels(device=device).set(stats.write_bytes)
                self.system_block_read_ops.labels(device=device).set(stats.read_count)
                self.system_block_write_ops.labels(device=device).set(stats.write_count)

                previous = self._previous_host_block.get(device)
                if previous:
                    elapsed = now - previous['timestamp']
                    self.system_block_read_bytes_rate.labels(device=device).set(
                        self._calculate_rate(previous['read_bytes'], stats.read_bytes, elapsed)
                    )
                    self.system_block_write_bytes_rate.labels(device=device).set(
                        self._calculate_rate(previous['write_bytes'], stats.write_bytes, elapsed)
                    )
                else:
                    self.system_block_read_bytes_rate.labels(device=device).set(0)
                    self.system_block_write_bytes_rate.labels(device=device).set(0)

                self._previous_host_block[device] = {
                    'timestamp': now,
                    'read_bytes': stats.read_bytes,
                    'write_bytes': stats.write_bytes,
                }
        except Exception as e:
            logger.error(f"Error collecting system block metrics: {e}")

    def collect_top_processes(self):
        try:
            processes = []
            for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info']):
                try:
                    processes.append(proc.info)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            top_cpu = sorted(processes, key=lambda p: p['cpu_percent'], reverse=True)[:5]
            for proc in top_cpu:
                self.top_cpu_processes.labels(pid=str(proc['pid']), name=proc['name']).set(proc['cpu_percent'])

            top_mem = sorted(processes, key=lambda p: p['memory_info'].rss, reverse=True)[:5]
            for proc in top_mem:
                self.top_memory_processes.labels(pid=str(proc['pid']), name=proc['name']).set(proc['memory_info'].rss)

        except Exception as e:
            logger.error(f"Error collecting top processes: {e}")

    def calculate_container_cpu_usage(self, stats):
        try:
            cpu_delta = stats['cpu_stats']['cpu_usage']['total_usage'] - stats['precpu_stats']['cpu_usage']['total_usage']
            system_delta = stats['cpu_stats']['system_cpu_usage'] - stats['precpu_stats']['system_cpu_usage']
            if system_delta > 0:
                online_cpus = stats['cpu_stats'].get('online_cpus', len(stats['cpu_stats']['cpu_usage'].get('percpu_usage', [1])))
                if online_cpus == 0:
                    online_cpus = psutil.cpu_count()
                return (cpu_delta / system_delta) * online_cpus * 100.0
            return 0.0
        except (KeyError, ZeroDivisionError, TypeError) as e:
            logger.warning(f"Error calculating CPU usage: {e}")
            return 0.0

    def _extract_container_block_bytes(self, stats):
        read_bytes = 0
        write_bytes = 0

        blkio_entries = stats.get('blkio_stats', {}).get('io_service_bytes_recursive') or []
        for entry in blkio_entries:
            operation = entry.get('op', '').lower()
            value = entry.get('value', 0)
            if operation == 'read':
                read_bytes += value
            elif operation == 'write':
                write_bytes += value

        return read_bytes, write_bytes

    def _calculate_container_memory_usage(self, stats):
        memory_stats = stats.get('memory_stats', {})
        usage = memory_stats.get('usage', 0)
        detailed_stats = memory_stats.get('stats', {}) or {}

        # Match docker stats more closely by subtracting reclaimable cache when available.
        reclaimable = (
            detailed_stats.get('inactive_file')
            or detailed_stats.get('total_inactive_file')
            or detailed_stats.get('cache')
            or 0
        )

        return max(usage - reclaimable, 0), memory_stats.get('limit', 0)

    def collect_docker_metrics(self):
        if not self.docker_client:
            return
        try:
            self.docker_client.ping()
        except Exception as e:
            logger.warning(f"Docker daemon not responding: {e}")
            return

        containers = self.docker_client.containers.list()
        logger.debug("Collecting Docker metrics for %s running containers", len(containers))

        def process_container(container):
            try:
                labels = container.labels
                project_name = labels.get('com.docker.compose.project', 'unknown')
                container_name = container.name
                stats = container.stats(stream=False)
                cpu_usage_percent = self.calculate_container_cpu_usage(stats)
                self.container_cpu_usage.labels(container_name=container_name, project=project_name).set(cpu_usage_percent)
                memory_usage, memory_limit = self._calculate_container_memory_usage(stats)
                self.container_memory_usage.labels(container_name=container_name, project=project_name).set(memory_usage)
                self.container_memory_limit.labels(container_name=container_name, project=project_name).set(memory_limit)

                now = time.time()
                network_stats = stats.get('networks', {}) or {}
                for interface, interface_stats in network_stats.items():
                    rx_bytes = interface_stats.get('rx_bytes', 0)
                    tx_bytes = interface_stats.get('tx_bytes', 0)
                    rx_packets = interface_stats.get('rx_packets', 0)
                    metric_labels = {
                        'container_name': container_name,
                        'project': project_name,
                        'interface': interface,
                    }

                    self.container_network_receive_bytes.labels(**metric_labels).set(rx_bytes)
                    self.container_network_transmit_bytes.labels(**metric_labels).set(tx_bytes)

                    network_key = (container_name, project_name, interface)
                    previous_network = self._previous_container_network.get(network_key)
                    if previous_network:
                        elapsed = now - previous_network['timestamp']
                        self.container_network_receive_bytes_rate.labels(**metric_labels).set(
                            self._calculate_rate(previous_network['rx_bytes'], rx_bytes, elapsed)
                        )
                        self.container_network_transmit_bytes_rate.labels(**metric_labels).set(
                            self._calculate_rate(previous_network['tx_bytes'], tx_bytes, elapsed)
                        )
                        self.container_network_receive_packets_rate.labels(**metric_labels).set(
                            self._calculate_rate(previous_network['rx_packets'], rx_packets, elapsed)
                        )
                    else:
                        self.container_network_receive_bytes_rate.labels(**metric_labels).set(0)
                        self.container_network_transmit_bytes_rate.labels(**metric_labels).set(0)
                        self.container_network_receive_packets_rate.labels(**metric_labels).set(0)

                    self._previous_container_network[network_key] = {
                        'timestamp': now,
                        'rx_bytes': rx_bytes,
                        'tx_bytes': tx_bytes,
                        'rx_packets': rx_packets,
                    }

                read_bytes, write_bytes = self._extract_container_block_bytes(stats)
                block_labels = {'container_name': container_name, 'project': project_name}
                self.container_block_read_bytes.labels(**block_labels).set(read_bytes)
                self.container_block_write_bytes.labels(**block_labels).set(write_bytes)

                previous_block = self._previous_container_block.get((container_name, project_name))
                if previous_block:
                    elapsed = now - previous_block['timestamp']
                    self.container_block_read_bytes_rate.labels(**block_labels).set(
                        self._calculate_rate(previous_block['read_bytes'], read_bytes, elapsed)
                    )
                    self.container_block_write_bytes_rate.labels(**block_labels).set(
                        self._calculate_rate(previous_block['write_bytes'], write_bytes, elapsed)
                    )
                else:
                    self.container_block_read_bytes_rate.labels(**block_labels).set(0)
                    self.container_block_write_bytes_rate.labels(**block_labels).set(0)

                self._previous_container_block[(container_name, project_name)] = {
                    'timestamp': now,
                    'read_bytes': read_bytes,
                    'write_bytes': write_bytes,
                }
            except Exception as e:
                logger.warning(f"Error collecting metrics for container {container.name}: {e}")

        with ThreadPoolExecutor(max_workers=10) as executor:
            list(executor.map(process_container, containers))

        logger.debug("Finished Docker metrics collection for %s running containers", len(containers))

    def collect_docker_compose_status(self, project_name=None):
        if not self.docker_client:
            return

        try:
            containers = self.docker_client.containers.list(all=True)
            running_count = 0
            for container in containers:
                labels = container.labels
                proj = labels.get('com.docker.compose.project', 'unknown')

                if project_name and proj != project_name:
                    continue

                try:
                    container.reload()  # Refresh container info
                    running = container.attrs['State'].get('Running', False)
                    status = 1 if running else 0
                    if running:
                        running_count += 1
                except Exception as e:
                    logger.warning(f"Error reading state for container {container.name}: {e}")
                    status = 0

                self.compose_container_status.labels(
                    container_name=container.name,
                    project=proj
                ).set(status)
                self.compose_container_status_flat.labels(name=container.name).set(status)

            logger.debug(
                "Collected Docker Compose status for %s containers (%s running)",
                len(containers),
                running_count,
            )

        except Exception as e:
            logger.error(f"Error collecting Docker Compose container status: {e}")

    def collect_all_metrics(self, collect_docker=False):
        started_at = time.time()
        self.collection_runs += 1
        logger.debug(
            "Starting metrics collection cycle %s (collect_docker=%s)",
            self.collection_runs,
            collect_docker,
        )

        self.collect_server_status()
        self.collect_system_cpu()
        self.collect_system_io_wait()
        self.collect_system_memory()
        self.collect_system_swap()
        self.collect_disk_usage()
        self.collect_system_network_io()
        self.collect_system_block_io()
        self.collect_postgres_metrics()
        self.collect_top_processes()

        # Always collect container status every loop
        if self.docker_client:
            self.collect_docker_compose_status()

        # Collect heavy Docker metrics less often
        if collect_docker and self.docker_client:
            connected, status = self.test_docker_connectivity()
            if connected:
                logger.debug("Docker connectivity check passed: %s", status)
                self.collect_docker_metrics()
            else:
                logger.warning("Skipping Docker metrics collection: %s", status)

        duration = time.time() - started_at
        if self.collection_runs == 1 or self.collection_runs % self.summary_log_every == 0:
            logger.info(
                "Completed metrics collection cycle %s in %.2fs (collect_docker=%s, tracked_host_interfaces=%s, tracked_host_devices=%s, tracked_container_networks=%s, tracked_container_devices=%s)",
                self.collection_runs,
                duration,
                collect_docker,
                len(self._previous_host_network),
                len(self._previous_host_block),
                len(self._previous_container_network),
                len(self._previous_container_block),
            )
        else:
            logger.debug(
                "Completed metrics collection cycle %s in %.2fs",
                self.collection_runs,
                duration,
            )

def main():
    metrics_port = int(os.getenv('METRICS_PORT', 8000))
    collection_interval = int(os.getenv('COLLECTION_INTERVAL', 5))
    docker_collection_interval = int(os.getenv('DOCKER_COLLECTION_INTERVAL', 30))

    logger.info(
        "Starting metrics collector (port=%s, collection_interval=%ss, docker_collection_interval=%ss, log_level=%s)",
        metrics_port,
        collection_interval,
        docker_collection_interval,
        log_level,
    )
    start_http_server(metrics_port)
    collector = MetricsCollector()
    loop_counter = 0

    try:
        while True:
            loop_counter += collection_interval
            collect_docker_now = (loop_counter % docker_collection_interval == 0)
            collector.collect_all_metrics(collect_docker=collect_docker_now)
            time.sleep(collection_interval)
    except KeyboardInterrupt:
        logger.info("Shutting down metrics collector...")

if __name__ == "__main__":
    psutil.cpu_percent(interval=0.1)
    main()