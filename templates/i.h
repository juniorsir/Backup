<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Termux Web Backup Suite</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.2/css/all.min.css">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/jstree/3.3.15/themes/default-dark/style.min.css" />
    <link rel="stylesheet" href="/static/style.css">
</head>
<body>
    <div class="container">
        <header>
            <h1><i class="fas fa-server"></i> Termux Web Backup Suite</h1>
            <div id="status-indicator" class="status-idle">
                <i class="fas fa-circle"></i> Idle
            </div>
        </header>

        <main>
            <div class="control-panel">
                <div class="form-section">
                    <h2><i class="fas fa-cogs"></i> Create Backup</h2>
                    
                    <h3><i class="fas fa-folder-tree"></i> Source Files</h3>
                    <p>Select files and folders to include in the backup.</p>
                    <div id="file-tree"></div>

                    <h3 class="subsection-header"><i class="fas fa-cog"></i> Backup Options</h3>
                    <div class="form-group">
                        <label for="jobName">Job Name <small>(for filename)</small></label>
                        <input type="text" id="jobName" placeholder="my-backup">
                    </div>
                    <div class="form-group">
                        <label for="exclude">Exclude Patterns <small>(space-separated)</small></label>
                        <input type="text" id="exclude" placeholder="*.tmp .thumbnails/ .cache/">
                    </div>

                    <div class="form-group">
                        <label for="encryption-method">Encryption Method</label>
                        <select id="encryption-method">
                            <option value="none" selected>None</option>
                            <option value="age">age (Modern, Passphrase)</option>
                            <option value="gpg">GPG (Legacy, Key-based)</option>
                        </select>
                    </div>
                    
                    <div class="form-group">
                        <label for="error-handling">On Permission/Read Error</label>
                        <select id="error-handling">
                            <option value="ignore" selected>Ignore Error and Continue (Recommended)</option>
                            <option value="abort">Abort Backup on First Error</option>
                        </select>
                    </div>

		    <!-- ... after the error-handling form-group ... -->
		    <div class="form-group">
		        <label style="display: inline-flex; align-items: center; gap: 10px;">
		            <input type="checkbox" id="show-file-progress" style="width: auto;">
		            <span>Show live file progress <small>(can be much slower)</small></span>
		        </label>
		    </div>
		    <!-- ... before the gpg-options div ... -->		    
                    <div id="gpg-options" class="encryption-options-wrapper hidden">
                        <div class="form-group">
                            <label for="gpgRecipient">GPG Recipient</label>
                            <input type="text" id="gpgRecipient" placeholder="Email or Key ID">
                        </div>
                    </div>

		    <!-- ... after the "show-file-progress" form-group ... -->
		    <div class="form-group">
		        <label style="display: inline-flex; align-items: center; gap: 10px;">
		            <input type="checkbox" id="backup-subdirs-individually" style="width: auto;">
		            <span>Backup each subdirectory individually</span>
       		        </label>
 		    </div>
   		    <p class="small-note" id="subdir-note" style="display: none;"><i class="fas fa-info-circle"></i> When this is checked, please select exactly one parent directory.</p>
		    <!-- ... before the gpg-options div ... -->

                    <div id="age-options" class="encryption-options-wrapper hidden">
                        <div class="form-group">
                            <label for="encryptionPassword">Encryption Passphrase</label>
                            <input type="password" id="encryptionPassword" placeholder="Enter a strong passphrase">
                        </div>
                    </div>
                
                    <!-- Find and replace this entire div in index.html -->
		    <div class="action-buttons">
		        <button id="start-local-btn"><i class="fas fa-save"></i> Save to Termux</button>
		        <button id="start-download-btn"><i class="fas fa-download"></i> Download to Browser</button>
		        
		    </div>
                </div>

                <hr class="section-divider">

                <div class="form-section restore-panel">
                    <h2><i class="fas fa-upload"></i> Restore from Backup</h2>
                    
                    <div class="restore-nav">
                        <button id="nav-restore-local" class="restore-nav-btn active">From Termux</button>
                        <button id="nav-restore-upload" class="restore-nav-btn">From Upload</button>
                    </div>

                    <div id="restore-local-panel" class="restore-content">
                        <p>Select a backup stored in `~/backups/`.</p>
                        <div class="form-group">
                            <div class="table-header">
                                <label>Available Backups</label>
                                <button id="refresh-backups-btn" title="Refresh List"><i class="fas fa-sync-alt"></i></button>
                            </div>
                            <div class="table-container">
                                <table class="backup-table">
                                    <thead>
                                        <tr>
                                            <th>Select</th>
                                            <th>Filename</th>
                                            <th>Size</th>
                                            <th>Date</th>
                                            <th>Actions</th>
                                        </tr>
                                    </thead>
                                    <tbody id="backup-table-body">
                                        <!-- Backup list will be inserted here by JavaScript -->
                                    </tbody>
                                </table>
                            </div>
                        </div>
                        <div class="action-buttons">
                            <button id="start-extraction-btn"><i class="fas fa-folder-open"></i> Extract Selected</button>
                        </div>
                    </div>
                    
                    <div id="restore-upload-panel" class="restore-content hidden">
                        <p>Choose a backup file from your device to upload and extract.</p>
                        <div class="form-group">
                            <label for="upload-file-input">Select Backup File</label>
                            <input type="file" id="upload-file-input" accept=".tar.zst,.gpg,.age">
                        </div>
                        <div class="action-buttons">
                            <button id="start-upload-btn"><i class="fas fa-cloud-upload-alt"></i> Upload & Extract</button>
                        </div>
                    </div>

                    <div class="restore-options-common">
                        <div id="age-restore-options" class="encryption-options-wrapper hidden">
                            <div class="form-group">
                                <label for="restorePassword">Decryption Passphrase</label>
                                <input type="password" id="restorePassword" placeholder="Enter passphrase for .age file">
                            </div>
                        </div>
                        <div id="gpg-restore-options" class="encryption-options-wrapper hidden">
                             <p><i class="fas fa-info-circle"></i> GPG decryption will use your local keyring.</p>
                        </div>
                    </div>
                </div>
            </div>

            <div class="monitor-panel">
                <h2><i class="fas fa-chart-line"></i> Live Monitor</h2>
                
                <div class="progress-section">
                    <div class="progress-bar-container">
                        <div id="progress-bar" class="progress-bar-fill"></div>
                    </div>
                    <div id="progress-text" class="progress-text">0.0%</div>
                    <div class="progress-details">
                        <span id="speed-indicator"><i class="fas fa-bolt"></i> -- MB/s</span>
                        <span id="eta-indicator"><i class="fas fa-hourglass-half"></i> ETA: --:--</span>
                    </div>
                </div>

                <div class="log-panel">
                    <h3><i class="fas fa-stream"></i> Live Log</h3>
                    <pre id="log-output">Welcome! Configure your backup or restore task.</pre>
                </div>
		<!-- ... after the log-panel div ... -->
		<div class="log-panel">
		    <h3><i class="fas fa-file-alt"></i> Processed Files</h3>
		    <pre id="file-log-output">Enable "Show live file progress" to see individual files here.</pre>
		</div>
		<!-- ... before the closing </main> tag ... -->
            </div>
        </main>
    </div>

    <!-- NEW: Modal for calculating size -->
    <div id="calculating-modal">
        <div class="modal-content">
            <div class="loader"></div>
            <h3>Calculating size...</h3>
            <p>This may take a moment for large directories.</p>
        </div>
    </div>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/jquery/3.7.1/jquery.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/jstree/3.3.15/jstree.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.min.js"></script>
    <script src="/static/app.js"></script>
</body>
</html>
