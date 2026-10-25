import os
import io
import time
import zipfile
import uuid
import psutil
from PIL import Image

import torch
import streamlit as st
from transformers import VisionEncoderDecoderModel, ViTImageProcessor, AutoTokenizer
from prometheus_client import (
    start_http_server,
    Counter,
    Gauge,
    Histogram,
    Summary,
    REGISTRY
)

# ---------------------------------------------------------
# 1. PAGE CONFIGURATION
# ---------------------------------------------------------
st.set_page_config(
    page_title="CPU Image Captioning Service",
    layout="wide"
)

# ---------------------------------------------------------
# 2. PROMETHEUS METRIC REGISTRATIONS (CACHED / SAFE REGISTRATION)
# ---------------------------------------------------------
@st.cache_resource
def get_metrics():
    def get_or_register(metric_cls, name, documentation, *args, **kwargs):
        if name in REGISTRY._names_to_collectors:
            return REGISTRY._names_to_collectors[name]
        return metric_cls(name, documentation, *args, **kwargs)

    images_processed = get_or_register(
        Counter,
        "caption_images_processed_total",
        "Total number of images processed for captioning",
        ["mode", "status", "model_name"]
    )

    client_requests = get_or_register(
        Counter,
        "caption_client_requests_total",
        "Total requests tagged with client session and IP metadata",
        ["client_ip", "session_id", "mode"]
    )

    active_requests = get_or_register(
        Gauge,
        "caption_requests_in_progress",
        "Number of inference requests currently being processed on CPU",
        ["mode"]
    )

    app_memory_usage = get_or_register(
        Gauge,
        "caption_app_memory_usage_bytes",
        "Current RSS memory consumed by the Streamlit application in bytes"
    )

    inference_latency = get_or_register(
        Histogram,
        "caption_inference_duration_seconds",
        "Time taken to generate caption for a single image in seconds",
        ["mode"],
        buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0]
    )

    bulk_payload_size = get_or_register(
        Summary,
        "caption_bulk_zip_size_bytes",
        "Summary distribution of uploaded bulk ZIP archive sizes in bytes",
        ["client_ip"]
    )

    return (
        images_processed,
        client_requests,
        active_requests,
        app_memory_usage,
        inference_latency,
        bulk_payload_size
    )

(
    IMAGES_PROCESSED,
    CLIENT_REQUESTS,
    ACTIVE_REQUESTS,
    APP_MEMORY_USAGE,
    INFERENCE_LATENCY,
    BULK_PAYLOAD_SIZE
) = get_metrics()

# ---------------------------------------------------------
# 3. METRICS EXPORTER SERVER INITIALIZATION
# ---------------------------------------------------------
@st.cache_resource
def start_metrics_server():
    try:
        start_http_server(8000)
    except OSError:
        pass
    return 8000

start_metrics_server()

# ---------------------------------------------------------
# 4. SESSION & SYSTEM TRACKING
# ---------------------------------------------------------
if "session_id" not in st.session_state:
    st.session_state["session_id"] = str(uuid.uuid4())[:8]

def get_client_ip():
    return "127.0.0.1"

def update_memory_gauge():
    process = psutil.Process(os.getpid())
    APP_MEMORY_USAGE.set(process.memory_info().rss)

# ---------------------------------------------------------
# 5. MODEL INITIALIZATION (CACHED)
# ---------------------------------------------------------
MODEL_NAME = "nlpconnect/vit-gpt2-image-captioning"

@st.cache_resource(show_spinner=False)
def load_model():
    device = torch.device("cpu")
    model = VisionEncoderDecoderModel.from_pretrained(MODEL_NAME).to(device)
    feature_extractor = ViTImageProcessor.from_pretrained(MODEL_NAME)
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    return model, feature_extractor, tokenizer, device

model, feature_extractor, tokenizer, device = load_model()
update_memory_gauge()

def run_caption(pil_image: Image.Image, mode: str) -> str:
    if pil_image.mode != "RGB":
        pil_image = pil_image.convert("RGB")
    
    start_time = time.time()
    try:
        pixel_values = feature_extractor(images=[pil_image], return_tensors="pt").pixel_values.to(device)
        with torch.no_grad():
            output_ids = model.generate(pixel_values, max_length=16, num_beams=4)
        
        caption = tokenizer.decode(output_ids[0], skip_special_tokens=True).strip()
        elapsed = time.time() - start_time
        
        INFERENCE_LATENCY.labels(mode=mode).observe(elapsed)
        IMAGES_PROCESSED.labels(mode=mode, status="success", model_name=MODEL_NAME).inc()
        return caption
    except Exception as err:
        IMAGES_PROCESSED.labels(mode=mode, status="error", model_name=MODEL_NAME).inc()
        raise err
    finally:
        update_memory_gauge()

# ---------------------------------------------------------
# 6. USER INTERFACE
# ---------------------------------------------------------
st.title("CPU Image Captioning Application")
st.caption(f"Prometheus Metrics: http://localhost:8000/metrics | Session ID: {st.session_state['session_id']}")

tab_single, tab_bulk = st.tabs(["Single Mode", "Bulk Mode (ZIP)"])

# Single Mode
with tab_single:
    st.subheader("Single Image Captioning")
    file = st.file_uploader("Upload an image (PNG, JPG, JPEG)", type=["png", "jpg", "jpeg"], key="single_up")
    
    if file is not None:
        img = Image.open(file)
        st.image(img, caption=file.name, use_container_width=True)
        
        if st.button("Generate Caption", key="btn_single"):
            ip = get_client_ip()
            sid = st.session_state["session_id"]
            CLIENT_REQUESTS.labels(client_ip=ip, session_id=sid, mode="single").inc()
            ACTIVE_REQUESTS.labels(mode="single").inc()
            
            with st.spinner("Processing image on CPU..."):
                t0 = time.time()
                try:
                    res = run_caption(img, mode="single")
                    duration = time.time() - t0
                    st.success(f"Caption: {res}")
                    st.info(f"Latency: {duration:.2f} seconds")
                except Exception as e:
                    st.error(f"Inference error: {e}")
                finally:
                    ACTIVE_REQUESTS.labels(mode="single").dec()
                    update_memory_gauge()

# Bulk Mode
with tab_bulk:
    st.subheader("Bulk Image Processing (ZIP Upload)")
    zip_file = st.file_uploader("Upload a ZIP file containing images", type=["zip"], key="bulk_up")
    
    if zip_file is not None:
        zip_bytes = zip_file.getvalue()
        file_size = len(zip_bytes)
        st.write(f"Archive Size: {file_size / (1024 * 1024):.2f} MB")
        
        if st.button("Process Bulk Archive", key="btn_bulk"):
            ip = get_client_ip()
            sid = st.session_state["session_id"]
            CLIENT_REQUESTS.labels(client_ip=ip, session_id=sid, mode="bulk").inc()
            BULK_PAYLOAD_SIZE.labels(client_ip=ip).observe(file_size)
            ACTIVE_REQUESTS.labels(mode="bulk").inc()
            
            table_rows = []
            progress_bar = st.progress(0.0)
            
            try:
                with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
                    entries = [
                        f for f in archive.namelist()
                        if f.lower().endswith(('.png', '.jpg', '.jpeg')) and not f.startswith('__MACOSX/')
                    ]
                    total = len(entries)
                    
                    if total == 0:
                        st.warning("No image files (.png, .jpg, .jpeg) found in ZIP archive.")
                    else:
                        start_time = time.time()
                        for idx, entry in enumerate(entries):
                            with archive.open(entry) as item:
                                img_obj = Image.open(io.BytesIO(item.read()))
                                cap = run_caption(img_obj, mode="bulk")
                                table_rows.append({"Filename": entry, "Caption": cap})
                            progress_bar.progress((idx + 1) / total)
                        
                        elapsed_total = time.time() - start_time
                        st.success(f"Processed {total} images in {elapsed_total:.2f} seconds.")
                        st.table(table_rows)
            except Exception as e:
                st.error(f"Error processing archive: {e}")
            finally:
                ACTIVE_REQUESTS.labels(mode="bulk").dec()
                update_memory_gauge()
