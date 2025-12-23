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
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Use host /proc if mounted
if os.path.exists("/host/proc"):
    logger.info("Using host /proc for psutil (reading host processes)")
    psutil.PROCFS_PATH = "/host/proc"
else:
    logger.info("Using container's /proc for psutil (container processes only)")

class MetricsCollector:
    def __init__(self):
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

        self.container_cpu_usage = Gauge('container_cpu_usage_percent', 'Container CPU usage percentage', ['container_name', 'project'])
        self.container_memory_usage = Gauge('container_memory_usage_bytes', 'Container memory usage in bytes', ['container_name', 'project'])
        self.container_memory_limit = Gauge('container_memory_limit_bytes', 'Container memory limit in bytes', ['container_name', 'project'])

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

    def test_docker_connectivity(self):
        if not self.docker_client:
            return False, "Docker client not initialized"
        try:
            version_info = self.docker_client.version()
            containers = self.docker_client.containers.list()
            return True, f"Connected to Docker {version_info.get('Version', 'unknown')}, found {len(containers)} containers"
        except Exception as e:
            return False, f"Docker connectivity test failed: {e}"

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

    def collect_docker_metrics(self):
        if not self.docker_client:
            return
        try:
            self.docker_client.ping()
        except Exception as e:
            logger.warning(f"Docker daemon not responding: {e}")
            return

        def process_container(container):
            try:
                labels = container.labels
                project_name = labels.get('com.docker.compose.project', 'unknown')
                container_name = container.name
                stats = container.stats(stream=False)
                cpu_usage_percent = self.calculate_container_cpu_usage(stats)
                self.container_cpu_usage.labels(container_name=container_name, project=project_name).set(cpu_usage_percent)
                memory_usage = stats['memory_stats'].get('usage', 0)
                memory_limit = stats['memory_stats'].get('limit', 0)
                self.container_memory_usage.labels(container_name=container_name, project=project_name).set(memory_usage)
                self.container_memory_limit.labels(container_name=container_name, project=project_name).set(memory_limit)
            except Exception as e:
                logger.warning(f"Error collecting metrics for container {container.name}: {e}")

        with ThreadPoolExecutor(max_workers=10) as executor:
            executor.map(process_container, self.docker_client.containers.list())

    def collect_docker_compose_status(self, project_name=None):
        if not self.docker_client:
            return

        try:
            containers = self.docker_client.containers.list(all=True)
            for container in containers:
                labels = container.labels
                proj = labels.get('com.docker.compose.project', 'unknown')

                if project_name and proj != project_name:
                    continue

                try:
                    container.reload()  # Refresh container info
                    running = container.attrs['State'].get('Running', False)
                    status = 1 if running else 0
                except Exception as e:
                    logger.warning(f"Error reading state for container {container.name}: {e}")
                    status = 0

                self.compose_container_status.labels(
                    container_name=container.name,
                    project=proj
                ).set(status)
                self.compose_container_status_flat.labels(name=container.name).set(status)

        except Exception as e:
            logger.error(f"Error collecting Docker Compose container status: {e}")

    def collect_all_metrics(self, collect_docker=False):
        self.collect_system_cpu()
        self.collect_system_memory()
        self.collect_system_swap()
        self.collect_disk_usage()
        self.collect_top_processes()

        # Always collect container status every loop
        if self.docker_client:
            self.collect_docker_compose_status()

        # Collect heavy Docker metrics less often
        if collect_docker and self.docker_client:
            connected, status = self.test_docker_connectivity()
            if connected:
                self.collect_docker_metrics()

def main():
    metrics_port = int(os.getenv('METRICS_PORT', 8000))
    collection_interval = int(os.getenv('COLLECTION_INTERVAL', 5))
    docker_collection_interval = int(os.getenv('DOCKER_COLLECTION_INTERVAL', 30))

    logger.info(f"Starting metrics collector on port {metrics_port}")
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

