#!/usr/bin/env python3
#
# Termux Web Backup Suite v16.0 (Final, Stable)
# This version integrates the "Save Subdirs" feature as a checkbox, modifying
# the behavior of the main backup actions for both local save and browser download
# (as a zip file). It includes all prior bug fixes and enhancements.

import os
import sys
import shutil
import subprocess
import json
import threading
import socket
import re
import atexit
import logging
import time
from datetime import datetime
from flask import Flask, render_template, request, jsonify, Response, stream_with_context
from flask_socketio import SocketIO
from urllib.parse import quote
from werkzeug.utils import secure_filename
import io
import zipfile

try:
    import qrcode
    QRCODE_PY_AVAILABLE = True
except ImportError:
    QRCODE_PY_AVAILABLE = False

# --- Debug Configuration ---
DEBUG_MODE = True

class TermColors:
    HEADER = '\033[95m'; OKBLUE = '\033[94m'; OKCYAN = '\033[96m'; OKGREEN = '\033[92m'
    WARNING = '\033[93m'; FAIL = '\033[91m'; ENDC = '\033[0m'; BOLD = '\033[1m'

# --- Globals ---
ROOT_NODE_CACHE = None
ROOT_NODE_CACHE_TIME = 0
print_lock = threading.Lock()

# --- Configuration ---
HOST = '0.0.0.0'; PORT = 8000
HOME_DIR = os.getenv("HOME")
PREFIX_DIR = "/data/data/com.termux/files/usr"
BACKUPS_PATH = os.path.join(HOME_DIR, "backups")
TEMP_UPLOAD_PATH = os.path.join(BACKUPS_PATH, "temp_uploads")
SHARED_STORAGE_PATH = os.path.join(HOME_DIR, "storage", "shared")
STDBUF_BIN = "/data/data/com.termux/files/usr/bin/stdbuf"; CAT_BIN = "/data/data/com.termux/files/usr/bin/cat"
TAR_BIN = "/data/data/com.termux/files/usr/bin/tar"; ZSTD_BIN = "/data/data/com.termux/files/usr/bin/zstd"
GPG_BIN = "/data/data/com.termux/files/usr/bin/gpg"; AGE_BIN = "/data/data/com.termux/files/usr/bin/age"
PV_BIN = "/data/data/com.termux/files/usr/bin/pv"; DU_BIN = "/data/data/com.termux/files/usr/bin/du"
WAKELOCK_BIN = "/data/data/com.termux/files/usr/bin/termux-wake-lock"
WAKEUNLOCK_BIN = "/data/data/com.termux/files/usr/bin/termux-wake-unlock"

app = Flask(__name__); app.config['SECRET_KEY'] = 'a_very_secret_key'
socketio = SocketIO(app, async_mode='threading')

# --- Helper & Logging Functions ---
def log_debug(message):
    if DEBUG_MODE: print(f"{TermColors.OKCYAN}[DEBUG]{TermColors.ENDC} {message}")

def log_event(message, level='info'):
    prefix_map = {'info': TermColors.OKGREEN, 'warn': TermColors.WARNING, 'error': TermColors.FAIL, 'success': TermColors.OKGREEN}
    prefix_color = prefix_map.get(level, TermColors.OKGREEN)
    prefix = f"{prefix_color}[{level.upper()}]{TermColors.ENDC}"
    print(f"{prefix} {message}")
    socketio.emit('log_message', {'level': level, 'message': message})

def monitor_process_stderr(process, stream_name, error_event=None, policy='ignore', failed_files_list=None, base_path=None):
    is_verbose_tar = (stream_name == 'tar' and any('v' in arg for arg in process.args))
    critical_errors = ["permission denied", "cannot open"]; ignorable_errors = ["broken pipe", "write error"]
    tar_error_re = re.compile(r"tar: (.*?): Cannot (?:open|read|stat)")

    with process.stderr as pipe:
        for line in iter(pipe.readline, b''):
            line_str = line.decode('utf-8', errors='ignore').strip()
            if not line_str: continue

            if is_verbose_tar and not line_str.startswith("tar: "):
                filename = line_str
                socketio.emit('file_processed', {'filename': filename})
                depth = filename.count(os.sep)
                indent = '  ' * depth
                basename = os.path.basename(filename) or filename
                with print_lock:
                    sys.stdout.write(f"{indent}✅ {basename}\n"); sys.stdout.flush()
                continue

            if stream_name == 'tar' and failed_files_list is not None and base_path:
                if match := tar_error_re.match(line_str):
                    failed_files_list.append(os.path.join(base_path, match.group(1).strip("'")))
            
            if any(err in line_str.lower() for err in ignorable_errors): continue
            socketio.emit('log_message', {'level': 'stderr', 'message': f"[{stream_name}] {line_str}"})

            if error_event and policy == 'abort' and any(err in line_str.lower() for err in critical_errors):
                log_event(f"Critical error in '{stream_name}': {line_str}. Aborting.", 'error')
                error_event.set(); break

def monitor_pv_progress(pv_process):
    percent_re, speed_re, eta_re = re.compile(r"(\d+\.?\d*)[%]"), re.compile(r"\[\s*(\d+\.?\d*\w+i?B/s)\s*\]"), re.compile(r"ETA\s+([\d:]+)")
    with pv_process.stderr as pipe:
        for line in iter(pipe.readline, b''):
            line_str = line.decode('utf-8', errors='ignore')
            percent, speed, eta = percent_re.search(line_str), speed_re.search(line_str), eta_re.search(line_str)
            progress_data = {'percent': f"{float(percent.group(1)):.1f}" if percent else "0.0", 'speed': speed.group(1) if speed else "--", 'eta': eta.group(1) if eta else "--:--"}
            if speed: socketio.emit('progress_update', progress_data)

def prune_redundant_paths(paths):
    if not paths: return []
    sorted_paths = sorted(list(set(os.path.abspath(p) for p in paths)))
    return [p for i, p in enumerate(sorted_paths) if not any(p.startswith(parent + os.sep) for parent in sorted_paths[:i])]

# --- Core Logic ---
def build_backup_pipeline(config):
    sources = config.get('sources', []);
    if not sources: raise ValueError("No source directories selected.")
    pruned_sources = prune_redundant_paths(sources)
    if not pruned_sources: raise ValueError("Source list is empty after pruning.")
    log_event(f"Pruned source list to: {pruned_sources}", "info")
    for path in pruned_sources:
        if not os.access(path, os.R_OK): raise PermissionError(f"Permission Denied for '{path}'.")
    
    total_size = 0; cache_map = {node['id']: node['data'].get('size_bytes', 0) for node in (ROOT_NODE_CACHE or [])}
    if all(p in cache_map for p in pruned_sources):
        total_size = sum(cache_map.get(p, 0) for p in pruned_sources)
        log_debug(f"Calculated total size from cache: {total_size} bytes")
    else:
        base = os.path.commonpath(pruned_sources) if len(pruned_sources)>1 else os.path.dirname(pruned_sources[0])
        rel_sources = [os.path.relpath(p, base) for p in pruned_sources]
        try:
            cmd = f"cd {quote(base)} && '{DU_BIN}' -sb {' '.join(map(quote, rel_sources))}"
            output = subprocess.check_output(cmd, shell=True, timeout=30).decode('utf-8')
            if output: total_size = sum(int(line.split()[0]) for line in output.strip().split('\n'))
        except Exception as e: log_event(f"Could not calculate total size (often OK): {e}", "warn")

    common_base = os.path.commonpath(pruned_sources) if len(pruned_sources)>1 else os.path.dirname(pruned_sources[0])
    relative_sources = [os.path.relpath(p, common_base) for p in pruned_sources]
    processes = []; error_policy = config.get('errorHandling', 'ignore')
    show_progress = str(config.get('showFileProgress')).lower() == 'true'

    tar_verb = "v" if show_progress else ""
    tar_cmd = [STDBUF_BIN, '-oL', TAR_BIN, f"-ch{tar_verb}f", "-"]
    if error_policy == 'ignore': tar_cmd.append("--ignore-failed-read")
    tar_cmd.extend(["-C", common_base, *relative_sources])
    
    pv_cmd = [PV_BIN, '-f', '-p', '-t', '-e', '-r', '-b', '-B', '256k']
    if total_size > 0: pv_cmd.extend(['-s', str(total_size)])

    tar_proc = subprocess.Popen(tar_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE); processes.append(("tar", tar_proc))
    pv_proc = subprocess.Popen(pv_cmd, stdin=tar_proc.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE); processes.append(("pv", pv_proc))
    zstd_proc = subprocess.Popen([ZSTD_BIN, "-T0"], stdin=pv_proc.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE); processes.append(("zstd", zstd_proc))
    tar_proc.stdout.close(); pv_proc.stdout.close()
    
    final_proc = zstd_proc
    if str(config.get('encrypt')).lower() == 'true':
        method = config.get('encryptionMethod'); last_out = zstd_proc.stdout
        if method == 'age':
            password = config.get('encryptionPassword');
            if not password: raise ValueError("Age encryption requires a passphrase.")
            env = os.environ.copy(); env['AGE_PASSPHRASE'] = password
            age_cmd = [AGE_BIN, "-p", "-o", "-"]
            final_proc = subprocess.Popen(age_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            threading.Thread(target=lambda s, d: (shutil.copyfileobj(s, d), s.close(), d.close()), args=(last_out, final_proc.stdin), daemon=True).start()
        elif method == 'gpg':
            recipient = config.get('gpgRecipient');
            if not recipient: raise ValueError("GPG encryption requires a recipient.")
            gpg_cmd = [GPG_BIN, "--encrypt", "--recipient", recipient, "--output", "-"]
            final_proc = subprocess.Popen(gpg_cmd, stdin=last_out, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            last_out.close()
        processes.append((method, final_proc))
        
    error_event = threading.Event(); failed_files = []
    
    threading.Thread(target=monitor_process_stderr, args=(tar_proc, 'tar', error_event, error_policy, failed_files, common_base), daemon=True).start()
    threading.Thread(target=monitor_pv_progress, args=(pv_proc,), daemon=True).start()
    threading.Thread(target=monitor_process_stderr, args=(zstd_proc, 'zstd'), daemon=True).start()
    if final_proc is not zstd_proc:
        threading.Thread(target=monitor_process_stderr, args=(final_proc, final_proc.args[0]), daemon=True).start()

    return final_proc.stdout, processes, error_event, failed_files

def build_extraction_pipeline(config, is_uploaded_file=False):
    filename = config.get('filename')
    source_path = os.path.join(TEMP_UPLOAD_PATH if is_uploaded_file else BACKUPS_PATH, filename)
    if not os.path.exists(source_path): raise FileNotFoundError(f"Backup file not found: {source_path}")

    processes = []; env = os.environ.copy()
    try: env['GPG_TTY'] = os.ttyname(sys.stdout.fileno())
    except Exception: log_event("Could not determine TTY for prompts.", "warn")

    cat_proc = subprocess.Popen([CAT_BIN, source_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE); processes.append(("cat", cat_proc))
    next_input = cat_proc.stdout
    
    if filename.endswith(".age"):
        age_cmd = [AGE_BIN, "--decrypt"]
        age_proc = subprocess.Popen(age_cmd, stdin=next_input, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        next_input.close(); processes.append(("age", age_proc)); next_input = age_proc.stdout
    elif filename.endswith(".gpg"):
        gpg_cmd = [GPG_BIN, "--decrypt"]
        gpg_proc = subprocess.Popen(gpg_cmd, stdin=next_input, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        next_input.close(); processes.append(("gpg", gpg_proc)); next_input = gpg_proc.stdout
        
    zstd_proc = subprocess.Popen([f"{ZSTD_BIN}cat"], stdin=next_input, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    processes.append(("zstd", zstd_proc)); next_input.close()

    show_progress = str(config.get('showFileProgress')).lower() == 'true'
    tar_verb = "v" if show_progress else ""
    tar_cmd = [TAR_BIN, f"-x{tar_verb}f", "-"]
    tar_proc = subprocess.Popen(tar_cmd, stdin=zstd_proc.stdout, stderr=subprocess.PIPE)
    processes.append(("tar", tar_proc)); zstd_proc.stdout.close()
    
    for name, proc in processes:
        threading.Thread(target=monitor_process_stderr, args=(proc, name), daemon=True).start()
        
    return processes

def generate_backup_filename(config):
    date_str = datetime.now().strftime('%d_%b').upper()
    sources = set(config.get('sources', []))
    termux_map = {HOME_DIR: "HOME", PREFIX_DIR: "USR"}
    termux_descriptors = sorted([name for path, name in termux_map.items() if path in sources])
    has_custom_paths = any(s not in termux_map for s in sources)
    storage_parts = []
    if termux_descriptors: storage_parts.append(f"TERMUX({'/'.join(termux_descriptors)})")
    if has_custom_paths: storage_parts.append("CUSTOM")
    storage_type_str = "+".join(storage_parts) or "EMPTY"
    base_filename = f"{date_str}_{storage_type_str}.tar.zst"
    if str(config.get('encrypt')).lower() == 'true':
        if config.get('encryptionMethod') == 'age': base_filename += ".age"
        elif config.get('encryptionMethod') == 'gpg': base_filename += ".gpg"
    return base_filename

def run_backup_task(config, destination_stream):
    pipeline_success, processes, failed_files = False, [], []
    output_path = destination_stream.name if hasattr(destination_stream, 'name') else "browser_stream"
    try:
        final_stream, processes, error_event, failed_files = build_backup_pipeline(config)
        with final_stream as pipe:
            while not error_event.is_set():
                chunk = pipe.read(8192)
                if not chunk: break
                destination_stream.write(chunk)
        if error_event.is_set(): raise RuntimeError("Backup aborted due to critical error.")
        exit_codes = {name: proc.wait() for name, proc in processes}
        tar_code = exit_codes.get('tar', 0)
        other_codes_ok = all(code == 0 for name, code in exit_codes.items() if name != 'tar')
        if (tar_code in [0, 1]) and other_codes_ok:
            pipeline_success = True
            if tar_code == 1: log_event("tar finished with warnings.", "warn")
            log_event("Backup task completed successfully!", 'success')
            socketio.emit('backup_complete', {'status': 'success', 'failed_files': failed_files})
        else: raise RuntimeError(f"Backup failed. Exit codes: {exit_codes}")
    except Exception as e:
        log_event(f"A critical error occurred: {e}", 'error')
        socketio.emit('backup_complete', {'status': 'error', 'failed_files': failed_files})
    finally:
        if not pipeline_success and output_path != "browser_stream" and os.path.exists(output_path):
            os.remove(output_path); log_event("Removed incomplete file.", 'warn')
        for _, proc in processes:
            if proc.poll() is None: proc.terminate()
            proc.wait()

def run_extraction_task(config, is_uploaded_file=False):
    temp_file = os.path.join(TEMP_UPLOAD_PATH, config.get('filename')) if is_uploaded_file else None
    try:
        processes = build_extraction_pipeline(config, is_uploaded_file)
        _, final_proc = processes[-1]; final_proc.wait()
        exit_codes = {name: proc.wait() for name, proc in processes}
        if all(code == 0 for code in exit_codes.values()):
            log_event("Extraction completed successfully!", 'success')
            socketio.emit('extraction_complete', {'status': 'success'})
        else:
            log_event(f"Extraction failed. Exit codes: {exit_codes}", 'error')
            socketio.emit('extraction_complete', {'status': 'error'})
    except Exception as e:
        log_event(f"A critical error during extraction: {e}", 'error')
        socketio.emit('extraction_complete', {'status': 'error'})
    finally:
        if temp_file and os.path.exists(temp_file):
            os.remove(temp_file); log_event("Cleaned up temporary file.", "info")

# --- Flask Routes & Startup ---
@app.route('/')
def index(): return render_template('index.html')

def get_human_readable_size(path):
    if not os.path.isdir(path): return ""
    try:
        cmd = [DU_BIN, "-sh", path]; result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=60)
        output = result.stdout.strip()
        if output:
            size = output.split()[0]
            if path == SHARED_STORAGE_PATH and (size == "0" or size == "0B"): return "(?)"
            return f"({size})"
        return "(?)"
    except Exception: return "(Error)"

def get_size_bytes(path):
    if not os.path.isdir(path): return 0
    try:
        cmd = [DU_BIN, "-sb", path]; result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=60)
        output = result.stdout.strip()
        if output: return int(output.split()[0])
        return 0
    except Exception: return 0

def task_pre_cache_root_nodes():
    global ROOT_NODE_CACHE, ROOT_NODE_CACHE_TIME
    print(f"{TermColors.BOLD}Pre-caching root directory sizes...{TermColors.ENDC}"); nodes = []
    root_paths = [
        {"text": "Shared Storage", "id": SHARED_STORAGE_PATH, "icon": "fa fa-mobile-alt"},
        {"text": "Termux Home", "id": HOME_DIR, "icon": "fa fa-terminal"},
        {"text": "Termux Prefix (usr)", "id": PREFIX_DIR, "icon": "fa fa-cogs"}]
    for root in root_paths:
        if root["id"] and os.path.exists(root["id"]):
            sys.stdout.write(f"  -> Calculating size for {root['text']}..."); sys.stdout.flush()
            size_str = get_human_readable_size(root["id"]); size_bytes = get_size_bytes(root["id"])
            sys.stdout.write(f" {size_str}\n"); sys.stdout.flush()
            nodes.append({
                "text": f"{root['text']} {size_str}".strip(), "id": root["id"],
                "data": {"path": root["id"], "size_bytes": size_bytes}, 
                "icon": root["icon"], "children": True})
    ROOT_NODE_CACHE = nodes; ROOT_NODE_CACHE_TIME = time.time()

@app.route('/api/get_tree_node')
def get_tree_node():
    path = request.args.get('path', '#')
    if path == '#': return jsonify(ROOT_NODE_CACHE or [])
    nodes = []
    try:
        if not os.access(path, os.R_OK): return jsonify([{"text": "Permission Denied", "icon": "fa fa-lock"}])
        for entry in sorted(os.listdir(path), key=str.lower):
            full_path = os.path.join(path, entry)
            if not os.path.lexists(full_path) or not os.access(full_path, os.R_OK): continue
            node = {"text": entry, "id": full_path, "data": {"path": full_path}}
            if os.path.isdir(full_path) and not os.path.islink(full_path):
                node["icon"], node["children"] = "fa fa-folder", True
            else:
                node["icon"], node["children"] = "fa fa-file", False
            nodes.append(node)
    except Exception as e: return jsonify([{"text": f"Error: {e}", "icon": "fa fa-exclamation-triangle"}])
    return jsonify(nodes)

@app.route('/start_local_backup', methods=['POST'])
def start_local_backup():
    config = request.json
    def task_wrapper(config):
        try:
            if str(config.get('backupSubdirs')).lower() == 'true':
                parent_path = config.get('parentPath')
                log_event(f"Starting individual subdirectory backup for '{parent_path}'...")
                os.makedirs(BACKUPS_PATH, exist_ok=True)
                subdirs = [d for d in sorted(os.listdir(parent_path)) if os.path.isdir(os.path.join(parent_path, d))]
                if not subdirs:
                    log_event(f"No subdirectories found in '{os.path.basename(parent_path)}'.", "warn")
                    socketio.emit('backup_complete', {'status': 'success'}); return
                total, completed = len(subdirs), 0
                for i, subdir_name in enumerate(subdirs):
                    log_event(f"[{i+1}/{total}] Backing up: {subdir_name}")
                    subdir_config = {k: v for k, v in config.items() if k != 'parentPath'}
                    subdir_config['sources'] = [os.path.join(parent_path, subdir_name)]
                    filename = generate_backup_filename(subdir_config)
                    output_path = os.path.join(BACKUPS_PATH, filename)
                    with open(output_path, "wb") as f:
                        run_backup_task(subdir_config, f)
                    completed += 1
                log_event(f"Subdirectory backup complete. {completed}/{total} archives created.", 'success')
                socketio.emit('backup_complete', {'status': 'success'})
            else:
                filename = generate_backup_filename(config)
                output_path = os.path.join(BACKUPS_PATH, filename)
                os.makedirs(BACKUPS_PATH, exist_ok=True)
                log_event(f"Saving to: {output_path}", 'info')
                with open(output_path, "wb") as f: run_backup_task(config, f)
        except Exception as e:
            log_event(f"Error in backup thread: {e}", "error")
            socketio.emit('backup_complete', {'status': 'error'})
    threading.Thread(target=task_wrapper, args=(config,), daemon=True).start()
    return jsonify({"status": "Local backup started."})

@app.route('/download_backup')
def download_backup():
    config = {k: v for k, v in request.args.items()}; config["sources"] = request.args.getlist('source')
    
    if str(config.get('backupSubdirs')).lower() == 'true':
        parent_path = config.get('parentPath')
        date_str = datetime.now().strftime('%d_%b').upper()
        zip_filename = f"{date_str}_{os.path.basename(parent_path)}_Subdirs.zip"
        
        def generate_zip_stream():
            log_event(f"Starting subdirectory backup to zip for '{os.path.basename(parent_path)}'...")
            subdirs = [d for d in sorted(os.listdir(parent_path)) if os.path.isdir(os.path.join(parent_path, d))]
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                total = len(subdirs)
                for i, subdir_name in enumerate(subdirs):
                    log_event(f"[{i+1}/{total}] Compressing '{subdir_name}' and adding to zip...")
                    subdir_config = {k: v for k, v in config.items() if k != 'parentPath'}
                    subdir_config['sources'] = [os.path.join(parent_path, subdir_name)]
                    archive_name = generate_backup_filename(subdir_config)
                    tar_stream, processes, error_event, _ = build_backup_pipeline(subdir_config)
                    with tar_stream:
                        archive_content = tar_stream.read()
                    for _, proc in processes:
                        if proc.poll() is None: proc.terminate()
                        proc.wait()
                    zip_file.writestr(archive_name, archive_content)
            log_event("Zip archive created. Starting stream to browser.", 'success')
            zip_buffer.seek(0); yield zip_buffer.getvalue()
        headers = {"Content-Disposition": f'attachment; filename="{quote(zip_filename)}"'}
        return Response(stream_with_context(generate_zip_stream()), headers=headers, content_type='application/zip')
    else:
        log_event("Request: Stream download.", 'info')
        try:
            final_stream, processes, error_event, _ = build_backup_pipeline(config)
        except (ValueError, PermissionError) as e: return f"Error: {e}", 400
        def generate_stream():
            try:
                with final_stream as pipe:
                    while not error_event.is_set():
                        chunk = pipe.read(8192)
                        if not chunk: break
                        yield chunk
            finally:
                log_event("Client disconnected. Cleaning up pipeline...", "info")
                for _, proc in processes:
                    if proc.poll() is None: proc.terminate()
                    proc.wait()
        filename = generate_backup_filename(config)
        headers = {"Content-Disposition": f'attachment; filename="{quote(filename)}"'}
        return Response(stream_with_context(generate_stream()), headers=headers, content_type='application/octet-stream')

@app.route('/api/list_backups')
def list_backups():
    if not os.path.isdir(BACKUPS_PATH): return jsonify([])
    try:
        backups = []
        for filename in sorted(os.listdir(BACKUPS_PATH), reverse=True):
            full_path = os.path.join(BACKUPS_PATH, filename)
            if not os.path.isfile(full_path): continue
            try:
                stat = os.stat(full_path)
                backups.append({"filename": filename,"size": f"{stat.st_size / 1024 / 1024:.2f} MB","modified": datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M')})
            except OSError: continue
        return jsonify(backups)
    except Exception as e: return jsonify({"error": f"Failed to list backups: {e}"}), 500

@app.route('/api/delete_backup', methods=['POST'])
def delete_backup():
    data = request.json; filename = data.get('filename')
    safe_filename = secure_filename(filename)
    full_path = os.path.join(BACKUPS_PATH, safe_filename)
    if not os.path.abspath(full_path).startswith(os.path.abspath(BACKUPS_PATH)): return jsonify({"error": "Access denied."}), 403
    try:
        if os.path.isfile(full_path):
            os.remove(full_path); log_event(f"Successfully deleted backup: {safe_filename}", "success")
            return jsonify({"status": "File deleted successfully."})
        else: return jsonify({"error": "File not found."}), 404
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/upload_and_extract', methods=['POST'])
def upload_and_extract():
    if 'backupFile' not in request.files: return jsonify({"error": "No file part"}), 400
    file = request.files['backupFile'];
    if file.filename == '': return jsonify({"error": "No file selected"}), 400
    filename = secure_filename(file.filename); os.makedirs(TEMP_UPLOAD_PATH, exist_ok=True)
    try:
        config = {k: v for k, v in request.form.items()}; config['filename'] = filename
        file.save(os.path.join(TEMP_UPLOAD_PATH, filename))
        threading.Thread(target=run_extraction_task, args=(config, True), daemon=True).start()
        return jsonify({"status": "Upload successful, extraction started."})
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/start_extraction', methods=['POST'])
def start_extraction():
    config = request.json
    threading.Thread(target=run_extraction_task, args=(config, False), daemon=True).start()
    return jsonify({"status": "Extraction started."})

def run_with_spinner(task, message="Processing..."):
    result = [None]; thread = threading.Thread(target=lambda: result.__setitem__(0, task()))
    thread.start(); i = 0
    while thread.is_alive():
        sys.stdout.write(f"\r{TermColors.BOLD}{message}{TermColors.ENDC} {'|/-\\'[i % 4]}"); sys.stdout.flush()
        time.sleep(0.1); i += 1
    thread.join(); sys.stdout.write('\r' + ' ' * (len(message) + 5) + '\r'); sys.stdout.flush()
    final_result = result[0]
    if isinstance(final_result, Exception): raise final_result
    elif final_result is True: print(f"{TermColors.BOLD}{message}{TermColors.ENDC} {TermColors.OKGREEN}[OK]{TermColors.ENDC}")
    else: print(f"{TermColors.BOLD}{message}{TermColors.ENDC} {TermColors.FAIL}[FAILED]{TermColors.ENDC}\n  {TermColors.WARNING}Reason: {final_result}{TermColors.ENDC}"); sys.exit(1)

def task_check_dependencies():
    deps = {'tar': TAR_BIN, 'zstd': ZSTD_BIN, 'pv': PV_BIN, 'gnupg': GPG_BIN, 'age': AGE_BIN, 'termux-api': WAKELOCK_BIN}
    missing = [name for name, path in deps.items() if not shutil.which(path)]
    if missing: return f"Missing dependencies: {', '.join(missing)}."
    return True

def task_check_storage_access():
    try:
        if os.path.exists(SHARED_STORAGE_PATH): os.listdir(SHARED_STORAGE_PATH); return True
        else: return "Shared storage path not found. Run 'termux-setup-storage'."
    except PermissionError: return "Shared storage not accessible. Run 'termux-setup-storage'."
    except Exception as e: return f"Unexpected storage error: {e}"

def task_acquire_wakelock():
    try:
        subprocess.run([WAKELOCK_BIN], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        atexit.register(release_wakelock); return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "Could not acquire wakelock via termux-api."

def release_wakelock():
    try: subprocess.run([WAKEUNLOCK_BIN], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception: pass

def get_lan_ip():
    try:
        if shutil.which("ip"):
            cmd = ["ip", "addr", "show", "wlan0"]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=2)
            if match := re.search(r'inet (\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', result.stdout):
                return match.group(1)
    except Exception: pass
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(0.1)
    try:
        s.connect(('10.255.255.255', 1)); return s.getsockname()[0]
    except Exception: return '127.0.0.1'
    finally: s.close()

def generate_and_display_qr(url):
    if QRCODE_PY_AVAILABLE:
        try:
            print(f"\n{TermColors.OKBLUE}[INFO] Generating QR code for terminal display...{TermColors.ENDC}\n")
            qr = qrcode.QRCode(); qr.add_data(url); qr.make(fit=True); qr.print_tty()
        except Exception as e: log_event(f"Could not generate QR code: {e}", "warn")
    else: print(f"\n{TermColors.WARNING}[WARN] Cannot generate QR code. Install: pip install qrcode{TermColors.ENDC}")

if __name__ == '__main__':
    log = logging.getLogger('werkzeug'); log.setLevel(logging.ERROR)
    
    print(f"{TermColors.HEADER}{TermColors.BOLD}--- Termux Web Backup Suite ---{TermColors.ENDC}")
    run_with_spinner(task_check_dependencies, "Checking dependencies...")
    run_with_spinner(task_check_storage_access, "Verifying storage access...")
    run_with_spinner(task_acquire_wakelock, "Acquiring wakelock...")
    
    task_pre_cache_root_nodes()

    print("-" * 30)

    ip_addr = get_lan_ip(); dashboard_url = f"http://{ip_addr}:{PORT}"
    print(f"{TermColors.BOLD}Connect by visiting: {TermColors.OKGREEN}{dashboard_url}{TermColors.ENDC}")
    generate_and_display_qr(dashboard_url)
    print(f"\n{TermColors.BOLD}-> Starting server on {TermColors.OKCYAN}{HOST}:{PORT}{TermColors.ENDC}... (Press Ctrl+C to stop)")
    
    socketio.run(app, host=HOST, port=PORT, allow_unsafe_werkzeug=True, log_output=False)
