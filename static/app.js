$(function () {
    // --- WebSocket Connection ---
    const socket = io();

    // --- Element Cache ---
    const elements = {
        startLocalBtn: $('#start-local-btn'),
        startDownloadBtn: $('#start-download-btn'),
        encryptionMethod: $('#encryption-method'),
        errorHandling: $('#error-handling'),
        gpgOptions: $('#gpg-options'),
        ageOptions: $('#age-options'),
        fileTree: $('#file-tree'),
        showFileProgress: $('#show-file-progress'),
        backupSubdirsIndividually: $('#backup-subdirs-individually'),
        subdirNote: $('#subdir-note'),
        navRestoreLocal: $('#nav-restore-local'),
        navRestoreUpload: $('#nav-restore-upload'),
        restoreLocalPanel: $('#restore-local-panel'),
        restoreUploadPanel: $('#restore-upload-panel'),
        refreshBackupsBtn: $('#refresh-backups-btn'),
        backupTableBody: $('#backup-table-body'),
        uploadFileInput: $('#upload-file-input'),
        startExtractionBtn: $('#start-extraction-btn'),
        startUploadBtn: $('#start-upload-btn'),
        ageRestoreOptions: $('#age-restore-options'),
        gpgRestoreOptions: $('#gpg-restore-options'),
        logOutput: $('#log-output'),
        fileLogOutput: $('#file-log-output'),
        statusIndicator: $('#status-indicator'),
        progressBar: $('#progress-bar'),
        progressText: $('#progress-text'),
        speedIndicator: $('#speed-indicator'),
        etaIndicator: $('#eta-indicator'),
        calculatingModal: $('#calculating-modal'),
    };

    // --- State ---
    let isJobRunning = false;
    let isModalVisible = false;

    // --- Screen Wake Lock Manager ---
    const wakeLockManager = {
        wakeLock: null,
        acquire: async function() {
            if ('wakeLock' in navigator) {
                try {
                    this.wakeLock = await navigator.wakeLock.request('screen');
                    logToScreen('Screen wake lock acquired.', 'info');
                } catch (err) {
                    logToScreen(`Could not acquire wake lock: ${err.name}, ${err.message}`, 'warn');
                }
            } else { logToScreen('Wake Lock API not supported. Please keep the screen on manually.', 'warn'); }
        },
        release: function() {
            if (this.wakeLock) {
                this.wakeLock.release().then(() => { this.wakeLock = null; logToScreen('Screen wake lock released.', 'info'); });
            }
        }
    };

    // --- Initialization ---
    elements.fileTree.jstree({
        'core': { 'data': { 'url': '/api/get_tree_node', 'data': (node) => ({ 'path': node.id }) }, 'themes': { 'name': 'default-dark', 'responsive': true } },
        'plugins': ['checkbox']
    });
    loadBackupFiles();

    // --- Event Handlers ---
    elements.startLocalBtn.on('click', () => startBackup('local'));
    elements.startDownloadBtn.on('click', () => startBackup('download'));
    elements.backupSubdirsIndividually.on('change', function() {
        elements.subdirNote.toggleClass('hidden', !$(this).is(':checked'));
    });
    elements.encryptionMethod.on('change', handleEncryptionMethodChange);
    elements.navRestoreLocal.on('click', () => switchRestoreTab('local'));
    elements.navRestoreUpload.on('click', () => switchRestoreTab('upload'));
    elements.refreshBackupsBtn.on('click', loadBackupFiles);
    elements.backupTableBody.on('change', 'input[name="backup-selection"]', () => handleFileSelectionChange($('input[name="backup-selection"]:checked').val()));
    elements.backupTableBody.on('click', '.delete-btn', handleDeleteClick);
    elements.uploadFileInput.on('change', () => handleFileSelectionChange(elements.uploadFileInput.val()));
    elements.startExtractionBtn.on('click', startLocalExtraction);
    elements.startUploadBtn.on('click', startUploadExtraction);

    // --- WebSocket Event Listeners ---
    socket.on('connect', () => logToScreen('Connected to backend.', 'info'));
    socket.on('log_message', (data) => logToScreen(data.message, data.level));
    socket.on('job_status_update', (data) => {
        if (isJobRunning && isModalVisible) {
            $('.modal-content h3').text(data.status_text);
        }
    });
    socket.on('progress_update', (data) => {
        hideCalculatingModal();
        elements.progressBar.css('width', data.percent + '%');
        elements.progressText.text(data.percent + '%');
        elements.speedIndicator.html(`<i class="fas fa-bolt"></i> ${data.speed}`);
        elements.etaIndicator.html(`<i class="fas fa-hourglass-half"></i> ETA: ${data.eta}`);
    });
    socket.on('file_processed', (data) => {
        if (!data || !data.filename) return;
        const filename = data.filename;
        const depth = (filename.match(/\//g) || []).length;
        const indent = ' '.repeat(depth * 2);
        const basename = filename.substring(filename.lastIndexOf('/') + 1) || filename;
        const logLine = `${indent}✅ ${basename}\n`;
        elements.fileLogOutput.append(logLine);
        elements.fileLogOutput.scrollTop(elements.fileLogOutput[0].scrollHeight);
    });
    socket.on('backup_complete', (data) => {
        hideCalculatingModal();
        handleJobCompletion(data, 'Backup');
    });
    socket.on('extraction_complete', (data) => {
        hideCalculatingModal();
        handleJobCompletion(data, 'Extraction');
    });

    // --- Core Functions ---
    function startBackup(type) {
        if (isJobRunning) return;
        const config = getBackupConfig();
        const selectedNodes = elements.fileTree.jstree(true).get_selected(true);

        if (config.backupSubdirs) {
            if (selectedNodes.length !== 1 || selectedNodes[0].original.children === false) {
                alert("When backing up subdirectories individually, you must select exactly one parent directory.");
                return;
            }
            config.parentPath = selectedNodes[0].id;
        } else {
            if (config.sources.length === 0) {
                alert("Please select one or more source files/folders.");
                return;
            }
        }

        showCalculatingModal();
        setUiState('running', config.backupSubdirs ? 'Backing up Subdirectories' : 'Backing up');

        if (type === 'local') {
            fetch('/start_local_backup', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(config) })
                .then(response => response.json())
                .catch(error => {
                    logToScreen(`Failed to contact server: ${error}`, 'error');
                    setUiState('idle', 'Error');
                    hideCalculatingModal();
                });
        } else if (type === 'download') {
            const params = new URLSearchParams();
            for (const key in config) {
                if (key === 'sources') {
                    config.sources.forEach(source => params.append('source', source));
                } else {
                    params.append(key, config[key]);
                }
            }
            window.location.href = `/download_backup?${params.toString()}`;
            setTimeout(() => {
                logToScreen("Download initiated. Server is preparing the stream...", "info");
                hideCalculatingModal();
                setUiState('idle', 'Idle');
            }, 6000);
        }
    }

    function startLocalExtraction() {
        if (isJobRunning) return;
        const filename = $('input[name="backup-selection"]:checked').val();
        if (!filename) { alert("Please select a backup file from the list to restore."); return; }
        const config = {
            filename: filename,
            showFileProgress: elements.showFileProgress.is(':checked')
        };
        setUiState('running', 'Extracting');
        fetch('/start_extraction', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(config) })
            .catch(error => {
                logToScreen(`Failed to contact server: ${error}`, 'error');
                setUiState('idle', 'Error');
            });
    }

    function startUploadExtraction() {
        if (isJobRunning) return;
        const fileInput = elements.uploadFileInput[0];
        if (!fileInput.files || fileInput.files.length === 0) { 
            alert("Please choose a file to upload."); 
            return; 
        }

        const file = fileInput.files[0];
        const fileSizeGB = file.size / (1024 * 1024 * 1024);

        // Soft warning for massive files, but allows them to proceed
        if (fileSizeGB > 2) {
            if (!confirm(`You are uploading a ${fileSizeGB.toFixed(1)} GB file.\n\nBrowser uploads for massive files can be slow and use a lot of temporary disk space. If this fails, move the file directly to '~/backups' on your server.\n\nContinue anyway?`)) {
                return;
            }
        }

        setUiState('running', 'Uploading...');
        logToScreen(`Starting upload of ${file.name} (${fileSizeGB.toFixed(2)} GB)...`, 'info');

        const formData = new FormData();
        formData.append('backupFile', file);
        formData.append('showFileProgress', elements.showFileProgress.is(':checked'));

        // Use XMLHttpRequest instead of fetch to track upload progress
        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/upload_and_extract', true);

        const startTime = Date.now();

        // Track Network Upload Progress
        xhr.upload.onprogress = function(e) {
            if (e.lengthComputable) {
                const percentComplete = (e.loaded / e.total) * 100;
                
                // Calculate Speed
                const timeElapsed = (Date.now() - startTime) / 1000; // in seconds
                const speedBps = e.loaded / timeElapsed;
                const speedMBps = (speedBps / (1024 * 1024)).toFixed(1);

                // Calculate ETA
                const bytesRemaining = e.total - e.loaded;
                const etaSeconds = isFinite(bytesRemaining / speedBps) ? Math.round(bytesRemaining / speedBps) : 0;
                const mins = Math.floor(etaSeconds / 60).toString().padStart(2, '0');
                const secs = (etaSeconds % 60).toString().padStart(2, '0');

                // Update UI directly
                elements.progressBar.css('width', percentComplete.toFixed(1) + '%');
                elements.progressText.text(percentComplete.toFixed(1) + '%');
                elements.speedIndicator.html(`<i class="fas fa-upload"></i> ${speedMBps} MB/s`);
                
                if (percentComplete === 100) {
                    elements.etaIndicator.html(`<i class="fas fa-cog fa-spin"></i> Saving to disk...`);
                    // Prevent spamming the log
                    if (!elements.logOutput.text().includes("Server is saving file")) {
                        logToScreen("Network upload complete. Server is now saving the file to disk (this may take a moment for large files)...", "warn");
                    }
                } else {
                    elements.etaIndicator.html(`<i class="fas fa-hourglass-half"></i> ETA: ${mins}:${secs}`);
                }
            }
        };

        // Handle Completion
        xhr.onload = function() {
            if (xhr.status === 200) {
                logToScreen("Upload saved successfully. Extraction pipeline starting...", "success");
                // The backend will now trigger WebSocket events which will take over the progress bar for extraction
            } else {
                let errStr = "Upload failed.";
                try { errStr = JSON.parse(xhr.responseText).error || errStr; } catch(e) {}
                logToScreen(`Error: ${errStr}`, 'error');
                setUiState('idle', 'Error');
            }
        };

        // Handle Network Errors
        xhr.onerror = function() {
            logToScreen("Network connection lost during upload.", 'error');
            setUiState('idle', 'Error');
        };

        // Send the request
        xhr.send(formData);
    }

    function handleDeleteClick() {
        if (isJobRunning) return;
        const filename = $(this).data('filename');
        if (confirm(`Are you sure you want to permanently delete this backup file?\n\n${filename}`)) {
            fetch('/api/delete_backup', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ filename: filename }) })
            .then(response => response.json())
            .then(data => {
                if (data.error) { logToScreen(`Error deleting file: ${data.error}`, 'error'); }
                else { logToScreen(data.status, 'success'); loadBackupFiles(); }
            })
            .catch(error => logToScreen(`Failed to send delete request: ${error}`, 'error'));
        }
    }

    function loadBackupFiles() {
        fetch('/api/list_backups').then(response => response.json()).then(data => {
            elements.backupTableBody.empty();
            if (data.length > 0) {
                data.forEach(backup => {
                    const row = `<tr>
                                    <td><input type="radio" name="backup-selection" value="${backup.filename}"></td>
                                    <td>${backup.filename}</td>
                                    <td>${backup.size}</td>
                                    <td>${backup.modified}</td>
                                    <td><button class="action-btn delete-btn" data-filename="${backup.filename}" title="Delete Backup"><i class="fas fa-trash-alt"></i></button></td>
                                 </tr>`;
                    elements.backupTableBody.append(row);
                });
            } else {
                elements.backupTableBody.append('<tr><td colspan="5" style="text-align:center;">No backup files found in ~/backups/</td></tr>');
            }
            handleFileSelectionChange('');
        }).catch(error => logToScreen(`Error fetching backup list: ${error}`, 'error'));
    }

    // --- Helper Functions ---
    function getBackupConfig() {
        const selectedNodes = elements.fileTree.jstree(true).get_selected(true);
        const sources = selectedNodes.map(node => node.id);
        const method = elements.encryptionMethod.val();
        return {
            sources: sources,
            encrypt: method !== 'none',
            encryptionMethod: method,
            errorHandling: $('#error-handling').val(),
            gpgRecipient: $('#gpgRecipient').val(),
            encryptionPassword: $('#encryptionPassword').val(),
            showFileProgress: elements.showFileProgress.is(':checked'),
            backupSubdirs: elements.backupSubdirsIndividually.is(':checked')
        };
    }

    function handleJobCompletion(data, jobType) {
        if (data.status === 'success') {
            updateStatus('Success', 'status-success');
            if (jobType === 'Backup') {
                elements.progressBar.css('width', '100%'); elements.progressText.text('100%');
                loadBackupFiles();
            }
        } else { updateStatus('Error', 'status-error'); }
        setUiState('idle', 'Idle');
    }
    
    function setUiState(state, statusText) {
        isJobRunning = (state === 'running');
        $('button, input').prop('disabled', isJobRunning);
        if (isJobRunning) {
            wakeLockManager.acquire();
            elements.logOutput.html('');
            elements.fileLogOutput.html('');
            resetProgress();
            updateStatus(statusText, 'status-running');
        } else {
            wakeLockManager.release();
            $('button, input').prop('disabled', false);
            updateStatus(statusText, 'status-idle');
        }
    }

    function logToScreen(message, level = 'info') {
        const logLine = $('<span></span>').addClass(`log-line ${level}`).text(message);
        elements.logOutput.append(logLine).append('\n');
        elements.logOutput.scrollTop(elements.logOutput[0].scrollHeight);
    }

    function showCalculatingModal() {
        $('.modal-content h3').text('Preparing... Calculating size...');
        elements.calculatingModal.css('display', 'flex').hide().fadeIn(200);
        isModalVisible = true;
    }

    function hideCalculatingModal() {
        if (isModalVisible) {
            elements.calculatingModal.fadeOut(200);
            isModalVisible = false;
        }
    }

    function handleEncryptionMethodChange() {
        const selectedMethod = $(this).val();
        elements.gpgOptions.toggleClass('hidden', selectedMethod !== 'gpg');
        elements.ageOptions.toggleClass('hidden', selectedMethod !== 'age');
    }

    function handleFileSelectionChange(filename) {
        filename = filename || '';
        elements.ageRestoreOptions.toggleClass('hidden', !filename.endsWith('.age'));
        elements.gpgRestoreOptions.toggleClass('hidden', !filename.endsWith('.gpg'));
    }

    function switchRestoreTab(tabName) {
        const isLocal = tabName === 'local';
        elements.navRestoreLocal.toggleClass('active', isLocal);
        elements.navRestoreUpload.toggleClass('active', !isLocal);
        elements.restoreLocalPanel.toggleClass('hidden', !isLocal);
        elements.restoreUploadPanel.toggleClass('hidden', isLocal);
        handleFileSelectionChange(isLocal ? $('input[name="backup-selection"]:checked').val() : elements.uploadFileInput.val());
    }

    function updateStatus(text, className) {
        elements.statusIndicator.html(`<i class="fas fa-circle"></i> ${text}`);
        elements.statusIndicator.attr('class', '').addClass(className);
    }
    
    function resetProgress() {
        elements.progressBar.css('width', '0%');
        elements.progressText.text('0.0%');
        elements.speedIndicator.html('<i class="fas fa-bolt"></i> -- MB/s');
        elements.etaIndicator.html('<i class="fas fa-hourglass-half"></i> ETA: --:--');
    }
});
