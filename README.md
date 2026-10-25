# DA3408 A4: CPU AI Observability with Prometheus & Grafana

This repository contains the complete observability and monitoring pipeline for a CPU-bound image captioning service built using Streamlit, Prometheus, and Grafana.

## Architecture & Components
- **Application (`app.py`):** CPU-only image captioning powered by `nlpconnect/vit-gpt2-image-captioning`. Supports both Single Mode and Bulk Mode (`.zip` ingestion).
- **Instrumentation (`prometheus_client`):** Exposes custom metrics on port `8000`:
  - `caption_images_processed_total` (Counter, partitioned by mode and status)
  - `caption_requests_in_progress` (Gauge, tracking active CPU inference concurrency)
  - `caption_app_memory_usage_bytes` (Gauge, tracking process RSS memory)
  - `caption_inference_duration_seconds` (Histogram, bucketed image latency)
  - `caption_bulk_zip_size_bytes` (Summary, tracking bulk payload sizes)
  - `caption_client_requests_total` (Counter, tracking client IP and session metadata)
- **Infrastructure (`node_exporter`):** Collects host-level CPU, RAM, and disk utilization metrics on port `9100`.
- **Scraper (`prometheus.yml`):** Scrapes application and host metrics at 2-second intervals with AI PromQL attributions.
- **Visualization (`dashboard.json`):** Production-grade Grafana dashboard designed in accordance with the 7 Commandments of Plotting.

## Running the Stack
1. Start `node_exporter` on port 9100.
2. Start Prometheus with `prometheus.yml` on port 9090.
3. Launch the Streamlit application: `streamlit run app.py`
4. Import `dashboard.json` into Grafana (`http://localhost:3000`).

---

## Fair Use of AI


1. **Application & Infrastructure:** The Streamlit application architecture, model inference loop, Prometheus metrics instrumentation, local `node_exporter` deployment, and scrape targets were designed, implemented, in a local Ubuntu environment. AI was used in debugging
2. **PromQL Formulation:** Generative AI was consulted as a tool to structure the syntax for complex PromQL aggregation queries used across the dashboard panels:
   * **Host CPU Utilization:** `100 - (avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[30s])) * 100)`
     * *Prompt:* "Write a PromQL expression using node_exporter to compute active host CPU utilization percentage."
   * **P95 Latency:** `histogram_quantile(0.95, sum by (le) (rate(caption_inference_duration_seconds_bucket[1m])))`
     * *Prompt:* "PromQL histogram_quantile syntax for 95th percentile latency from bucketed metrics."
   * **Throughput:** `sum by (mode) (rate(caption_images_processed_total{status="success"}[30s]))`
     * *Prompt:* "PromQL rate query for per-second counter increments grouped by mode."
   * **Average Latency:** `rate(caption_inference_duration_seconds_sum[1m]) / rate(caption_inference_duration_seconds_count[1m])`
     * *Prompt:* "Calculate average latency using Prometheus histogram sum and count metrics."
3. **Dashboard Construction:** Panel layouts, visual scales, thresholds, and adherence to the 7 Commandments of Plotting were configured and validated during live workload execution in Grafana.
