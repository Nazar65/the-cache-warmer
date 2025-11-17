import argparse
import csv
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from rich import align, print
from rich.live import Live
from rich.logging import RichHandler
from rich.panel import Panel
from rich.progress import Progress
from rich.table import Table


def warm_url(url, progress, task, headers=None, cookies=None, timeout=10):
    """Send a GET request to warm up a URL with specified headers and cookies"""
    try:
        response = requests.get(url, headers=headers, cookies=cookies, timeout=timeout)
        # Extract x-cache header
        x_cache = response.headers.get("x-cache", "Not found")

        progress.update(task, advance=1)

        return {
            "url": url,
            "status": response.status_code,
            "success": True,
            "error": None,
            "x_cache": x_cache,
        }
    except Exception as e:
        progress.update(task, advance=1)
        print(
            f"[red bold]There is an error during request run please check log files for more information url: "
            + url
        )

        log = get_logger()
        log.error("Error running request: ", exc_info=e)

        return {
            "url": url,
            "status": None,
            "success": False,
            "error": str(e),
            "x_cache": None,
        }


def get_logger() -> logging.Logger:
    FORMAT = "%(message)s"
    logging.basicConfig(
        level="NOTSET",
        format=FORMAT,
        datefmt="[%X]",
        handlers=[RichHandler()],
    )
    return logging.getLogger("rich")


def read_urls_from_csv(filename):
    """Read URLs from CSV file (assuming first column contains URLs)"""
    urls = []
    try:
        with open(filename, "r", newline="", encoding="utf-8") as csvfile:
            reader = csv.reader(csvfile)
            for row in reader:
                if row:  # Skip empty rows
                    urls.append(row[0].strip())
    except FileNotFoundError:
        print(f"Error: File '{filename}' not found.")
        return []
    except Exception as e:
        print(f"Error reading CSV file: {e}")
        return []
    return urls


def process_urls_threaded(
    urls,
    progress,
    task,
    max_workers=5,
    timeout=50,
    custom_headers=None,
    custom_cookies=None,
):
    """Process URLs in parallel using ThreadPoolExecutor"""
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_url = {
            executor.submit(
                warm_url, url, progress, task, custom_headers, custom_cookies, timeout
            ): url
            for url in urls
        }
        # Collect results as they complete
        for future in future_to_url:
            result = future.result()
            results.append(result)
    return results


def generate_stage_status_result_table(hit_count, miss_count, all_urls) -> Table:
    stage_status = Table(
        title="[bold magenta]FINAL STAGE SUMMARY: [green]:heavy_check_mark:"
    )
    stage_status.add_column("Total URLs processed", style="cyan", no_wrap=True)
    stage_status.add_column("Total Cache HIT:", style="green")
    stage_status.add_column("Total Cache MISS:", style="red")
    stage_status.add_column("Total time:", style="blue")
    stage_status.add_row(
        str(all_urls),
        str(hit_count),
        str(miss_count),
    )
    return stage_status


def read_configuration_file(filename) -> dict:
    try:
        with open(filename, "r") as config_file:
            return json.load(config_file)
    except FileNotFoundError as e:
        print(f"[red bold]No configuration file found config.json")
        logger = get_logger()
        logger.error("Error reading configuration file: ", exc_info=e)

        return json.loads("{}")


def main():
    parser = argparse.ArgumentParser(description="URL Cache Warmer")
    parser.add_argument(
        "--files", nargs="+", required=True, help="CSV files containing URLs to warm up"
    )
    parser.add_argument(
        "--threads", type=int, default=5, help="Number of threads to use (default: 5)"
    )
    args = parser.parse_args()

    # Validate threads parameter
    if args.threads <= 0:
        print("Error: Threads must be a positive integer")
        sys.exit(1)

    all_urls = []
    for csv_file in args.files:
        urls = read_urls_from_csv(csv_file)
        if not urls:
            print(f"[bold red]No URLs found in {csv_file}.")
            continue
        print(f"[bold green]Found {len(urls)} URLs in [bold red]{csv_file}.")
        all_urls.extend(urls)

    if not all_urls:
        print("No URLs found in any of the provided files.")
        return

    print(f"[bold magenta]Total URLs to warm up: [bold green]{len(all_urls)} \n")

    configurations = read_configuration_file("config.json")

    # Run warmup for both configurations
    total_hit_count = 0
    total_miss_count = 0
    total_time = 0

    for i, config in enumerate(configurations):
        progress = Progress()
        progress.start()
        task = progress.add_task(
            f"  STAGE {i+1}: {config['name']}", total=len(all_urls)
        )
        # Warm up URLs in parallel
        start_time = time.time()

        # Process URLs in parallel
        results = process_urls_threaded(
            all_urls,
            progress,
            task,
            max_workers=args.threads,
            timeout=10,
            custom_headers=config.get("headers", {}),
            custom_cookies=config.get("cookies", {}),
        )

        # Count hits and misses
        hit_count = 0
        miss_count = 0

        for result in results:
            if result["success"] and result["x_cache"]:
                x_cache = result["x_cache"]
                if "HIT" in x_cache.upper():
                    hit_count += 1
                elif "MISS" in x_cache.upper():
                    miss_count += 1

        # Print summary with colored output
        end_time = time.time()

        progress.stop()
        print("\n")
        print(
            Panel(
                f"[magenta] Total URLs processed:  [green]{len(all_urls)}[/green] \n Cache HIT: [green]{hit_count}[/green] \n Cache MISS: [red]{miss_count}[/red] \n Total time: [green]{end_time - start_time:.2f}[/green] seconds",
                title=f"[white]{config['name']} SUMMARY",
                subtitle=f"Done :heavy_check_mark:",
                width=35,
                height=8,
                padding=1,
            )
        )
        print("\n")

        total_hit_count += hit_count
        total_miss_count += miss_count
        total_time += end_time - start_time

    # Print final summary
    table = Table(
        title="[bold magenta]FINAL AGGREGATE SUMMARY: [green]:heavy_check_mark:"
    )
    table.add_column("Total URLs processed", style="cyan", no_wrap=True)
    table.add_column("Total Cache HIT:", style="green")
    table.add_column("Total Cache MISS:", style="red")
    table.add_column("Total Time:", style="red")

    with Live(table, refresh_per_second=4) as live:
        table.add_row(
            str(len(all_urls) * len(configurations)),
            str(total_hit_count),
            str(total_miss_count),
            str(f"{total_time:.2f}"),
        )


if __name__ == "__main__":
    main()
