// RittDoc Editor - Main Application JavaScript

// Global state
const APP_STATE = {
    pdfDoc: null,
    currentPage: 1,
    totalPages: 0,
    scale: 1.5,
    xmlContent: '',
    htmlContent: '',
    monacoEditor: null,
    currentView: 'xml',
    screenshotMode: false,
    screenshotData: null,
    mediaFiles: [],
    isHtmlEdited: false,
    pageElements: [],
    pageToXmlMapping: {}, // Maps page numbers to XML element info
    xmlToPageMapping: {}, // Maps XML element indices to page numbers
    totalXmlPages: 0, // Total unique pages found in XML
    syncInProgress: false, // Flag to prevent circular scroll updates
    htmlScrollListener: null,
    htmlScrollTarget: null,
    xmlScrollDisposable: null,
    showBlockOverlay: false // Toggle for showing block info overlay
};

// PDF.js worker
pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';

// Initialize application
document.addEventListener('DOMContentLoaded', () => {
    initializeApp();
});

async function initializeApp() {
    console.log('Initializing RittDoc Editor...');
    
    // Load initial data
    await loadInitialData();
    
    // Initialize Monaco Editor
    initializeMonacoEditor();
    
    // Setup event listeners
    setupEventListeners();
    
    // Load PDF
    if (APP_STATE.pdfPath) {
        await loadPDF();
    }
    
    // Load media files
    await loadMediaFiles();
    
    // Setup page mapping
    await setupPageMapping();
    
    console.log('RittDoc Editor initialized successfully');
}

// Load initial data from server
async function loadInitialData() {
    try {
        showLoading();
        const response = await fetch('/api/init');
        const data = await response.json();
        
        if (data.error) {
            showNotification(data.error, 'error');
            return;
        }
        
        APP_STATE.xmlContent = data.xml;
        APP_STATE.htmlContent = data.html;
        APP_STATE.pdfPath = data.pdf.path;
        APP_STATE.multimediaFolder = data.multimedia_folder;
        
        hideLoading();
    } catch (error) {
        console.error('Error loading initial data:', error);
        showNotification('Failed to load initial data', 'error');
        hideLoading();
    }
}

// Initialize Monaco Editor
function initializeMonacoEditor() {
    require.config({ paths: { vs: 'https://cdn.jsdelivr.net/npm/monaco-editor@0.44.0/min/vs' } });
    
    require(['vs/editor/editor.main'], function () {
        APP_STATE.monacoEditor = monaco.editor.create(document.getElementById('xmlEditor'), {
            value: APP_STATE.xmlContent,
            language: 'xml',
            theme: 'vs',
            automaticLayout: true,
            fontSize: 14,
            minimap: { enabled: true },
            scrollBeyondLastLine: false,
            wordWrap: 'on',
            folding: true,
            lineNumbers: 'on'
        });
        
        console.log('Monaco Editor initialized');
    });
}

// Initialize HTML editable content
function initializeHTMLEditor() {
    const editableContent = document.getElementById('htmlEditableContent');
    if (editableContent) {
        // Track changes
        editableContent.addEventListener('input', () => {
            APP_STATE.isHtmlEdited = true;
        });

        // Handle paste - preserve some formatting
        editableContent.addEventListener('paste', (e) => {
            e.preventDefault();
            const text = (e.originalEvent || e).clipboardData.getData('text/html') ||
                        (e.originalEvent || e).clipboardData.getData('text/plain');
            document.execCommand('insertHTML', false, text);
        });

        // Keyboard shortcuts
        editableContent.addEventListener('keydown', (e) => {
            // Ctrl+B for bold
            if (e.ctrlKey && e.key === 'b') {
                e.preventDefault();
                document.execCommand('bold');
            }
            // Ctrl+I for italic
            else if (e.ctrlKey && e.key === 'i') {
                e.preventDefault();
                document.execCommand('italic');
            }
            // Ctrl+U for underline
            else if (e.ctrlKey && e.key === 'u') {
                e.preventDefault();
                document.execCommand('underline');
            }
            // Ctrl+Z for undo
            else if (e.ctrlKey && e.key === 'z') {
                e.preventDefault();
                document.execCommand('undo');
            }
            // Ctrl+Y for redo
            else if (e.ctrlKey && e.key === 'y') {
                e.preventDefault();
                document.execCommand('redo');
            }
        });

        console.log('HTML Editor initialized');
    }

    // Setup toolbar buttons
    setupRichTextToolbar();

    // Initialize table editor
    initializeTableEditor();
}

// Setup event listeners
function setupEventListeners() {
    // View mode buttons
    document.getElementById('xmlViewBtn').addEventListener('click', () => switchView('xml'));
    document.getElementById('htmlViewBtn').addEventListener('click', () => switchView('html'));
    document.getElementById('htmlEditBtn').addEventListener('click', () => switchView('htmledit'));
    
    // Save buttons - show confirmation dialog before saving
    document.getElementById('saveBtn').addEventListener('click', () => showSaveConfirmDialog());
    document.getElementById('saveReprocessBtn').addEventListener('click', () => showSaveConfirmDialog());
    
    // PDF controls
    document.getElementById('prevPageBtn').addEventListener('click', () => changePage(-1));
    document.getElementById('nextPageBtn').addEventListener('click', () => changePage(1));
    document.getElementById('pageNumberInput').addEventListener('change', (e) => {
        const page = parseInt(e.target.value);
        if (page >= 1 && page <= APP_STATE.totalPages) {
            scrollToPage(page);
        }
    });
    
    // Zoom controls
    document.getElementById('zoomInBtn').addEventListener('click', () => changeZoom(0.25));
    document.getElementById('zoomOutBtn').addEventListener('click', () => changeZoom(-0.25));
    
    // Screenshot button
    document.getElementById('screenshotBtn').addEventListener('click', toggleScreenshotMode);
    
    // Editor controls
    document.getElementById('formatBtn').addEventListener('click', formatXML);
    document.getElementById('validateBtn').addEventListener('click', validateXML);
    document.getElementById('refreshHtmlBtn').addEventListener('click', refreshHTMLPreview);
    
    // Thumbnail toggle
    document.getElementById('toggleThumbnailsBtn').addEventListener('click', toggleThumbnails);
    
    // PDF scroll - only update page number, no cross-panel sync
    const pdfViewer = document.getElementById('pdfViewer');
    if (pdfViewer) {
        pdfViewer.addEventListener('scroll', updatePageNumberFromScroll);
    }

    // Set initial zoom level display
    document.getElementById('zoomLevel').textContent = `${Math.round(APP_STATE.scale * 100)}%`;

    // Resizer
    setupResizer();
}

// Switch view mode
function switchView(view) {
    APP_STATE.currentView = view;

    // Update button states
    document.querySelectorAll('.mode-btn').forEach(btn => btn.classList.remove('active'));

    // Hide all views
    document.getElementById('xmlEditor').style.display = 'none';
    document.getElementById('htmlPreview').style.display = 'none';
    document.getElementById('htmlEditor').style.display = 'none';
    document.getElementById('refreshHtmlBtn').style.display = 'none';
    document.getElementById('formatBtn').style.display = 'inline-flex';
    document.getElementById('validateBtn').style.display = 'inline-flex';

    // Hide table toolbar when switching away from HTML edit mode
    const tableToolbar = document.getElementById('tableEditorToolbar');
    if (tableToolbar) {
        tableToolbar.style.display = 'none';
    }
    
    // Show selected view
    if (view === 'xml') {
        document.getElementById('xmlViewBtn').classList.add('active');
        document.getElementById('xmlEditor').style.display = 'block';
        document.getElementById('editorTitle').innerHTML = '<i class="fas fa-code"></i> XML Editor';
    } else if (view === 'html') {
        document.getElementById('htmlViewBtn').classList.add('active');
        const htmlPreview = document.getElementById('htmlPreview');
        htmlPreview.style.display = 'block';

        // Apply enhanced HTML rendering with fonts and images
        htmlPreview.innerHTML = enhanceHTMLContent(APP_STATE.htmlContent);

        document.getElementById('refreshHtmlBtn').style.display = 'inline-flex';
        document.getElementById('formatBtn').style.display = 'none';
        document.getElementById('validateBtn').style.display = 'none';
        document.getElementById('editorTitle').innerHTML = '<i class="fas fa-eye"></i> HTML Preview';

        // Reapply block overlays if enabled
        if (APP_STATE.showBlockOverlay) {
            setTimeout(() => applyBlockOverlays(), 100);
        }
        
    } else if (view === 'htmledit') {
        document.getElementById('htmlEditBtn').classList.add('active');
        document.getElementById('htmlEditor').style.display = 'flex';

        // Load HTML content into editable div
        const editableContent = document.getElementById('htmlEditableContent');
        if (!APP_STATE.isHtmlEdited) {
            editableContent.innerHTML = enhanceHTMLContent(APP_STATE.htmlContent);
        }
        initializeHTMLEditor();

        document.getElementById('formatBtn').style.display = 'none';
        document.getElementById('validateBtn').style.display = 'none';
        document.getElementById('editorTitle').innerHTML = '<i class="fas fa-edit"></i> HTML Editor';

        // Reapply block overlays if enabled
        if (APP_STATE.showBlockOverlay) {
            setTimeout(() => applyBlockOverlays(), 100);
        }
    }
}

// Enhance HTML content with proper font styling and image paths
function enhanceHTMLContent(html) {
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, 'text/html');
    
    // Extract font specifications
    const fontSpecs = {};
    const fontSpecElements = doc.querySelectorAll('[class*="font-spec"], [id*="font-spec"]');
    fontSpecElements.forEach(spec => {
        const id = spec.getAttribute('id') || spec.getAttribute('class');
        if (id) {
            const fontFamily = spec.getAttribute('data-font-family') || spec.style.fontFamily;
            const fontSize = spec.getAttribute('data-font-size') || spec.style.fontSize;
            const fontColor = spec.getAttribute('data-font-color') || spec.style.color;
            
            fontSpecs[id] = { fontFamily, fontSize, fontColor };
        }
    });
    
    // Apply font specs to elements that reference them
    const elementsWithFontRef = doc.querySelectorAll('[data-font-spec], [class*="font-"], .phrase, span[style]');
    elementsWithFontRef.forEach(el => {
        const fontRef = el.getAttribute('data-font-spec') || el.className.match(/font-\d+/)?.[0];
        if (fontRef && fontSpecs[fontRef]) {
            const spec = fontSpecs[fontRef];
            if (spec.fontFamily) el.style.fontFamily = spec.fontFamily;
            if (spec.fontSize) el.style.fontSize = spec.fontSize;
            if (spec.fontColor) el.style.color = spec.fontColor;
        }
        
        // Also check for inline styles
        const style = el.getAttribute('style');
        if (style) {
            // Preserve existing styles
            el.setAttribute('style', style);
        }
    });
    
    // Fix image paths and add smooth loading containers
    const images = doc.querySelectorAll('img');
    images.forEach(img => {
        let src = img.getAttribute('src');
        if (src && !src.startsWith('http') && !src.startsWith('data:')) {
            // Extract just the filename
            const filename = src.split('/').pop();
            // Update to use API endpoint
            img.setAttribute('src', `/api/media/${filename}`);
        }

        // Wrap standalone images (not in figure) with a loading container
        if (!img.closest('figure') && !img.closest('.figure') && !img.closest('.docbook-figure') && !img.closest('.media-figure')) {
            const wrapper = doc.createElement('div');
            wrapper.className = 'image-loading-wrapper';
            img.parentNode.insertBefore(wrapper, img);
            wrapper.appendChild(img);
        }

        // Add loading class and event handlers
        img.classList.add('image-loading');
        img.setAttribute('loading', 'lazy');
        img.setAttribute('onload', 'this.classList.remove("image-loading"); this.classList.add("image-loaded"); if(this.parentNode.classList.contains("image-loading-wrapper")) this.parentNode.classList.add("loaded");');
        img.setAttribute('onerror', 'this.classList.remove("image-loading"); this.classList.add("image-error"); this.src="/api/placeholder-image";');
    });

    // Handle figure elements
    const figures = doc.querySelectorAll('figure, .figure, .docbook-figure, .media-figure');
    figures.forEach(fig => {
        // Add smooth loading class to figures
        fig.classList.add('figure-loading-container');

        const img = fig.querySelector('img');
        if (img) {
            let src = img.getAttribute('src');
            if (src && !src.startsWith('http') && !src.startsWith('data:')) {
                const filename = src.split('/').pop();
                img.setAttribute('src', `/api/media/${filename}`);
            }
            // Add loading behavior
            img.classList.add('image-loading');
            img.setAttribute('loading', 'lazy');
            img.setAttribute('onload', 'this.classList.remove("image-loading"); this.classList.add("image-loaded"); this.closest(".figure-loading-container")?.classList.add("loaded");');
            img.setAttribute('onerror', 'this.classList.remove("image-loading"); this.classList.add("image-error"); this.src="/api/placeholder-image";');
        }
    });
    
    // Add data-page attributes to sections for syncing
    const sections = doc.querySelectorAll('section, div[class*="chapter"], div[class*="section"], article');
    sections.forEach((section, index) => {
        section.setAttribute('data-section-id', `section-${index}`);
    });
    
    return doc.body.innerHTML;
}

// Setup HTML scroll sync
function setupHtmlScrollSync(element) {
    if (APP_STATE.htmlScrollListener && APP_STATE.htmlScrollTarget) {
        APP_STATE.htmlScrollTarget.removeEventListener('scroll', APP_STATE.htmlScrollListener);
    }
    
    if (!element) {
        APP_STATE.htmlScrollTarget = null;
        return;
    }
    
    APP_STATE.htmlScrollTarget = element;
    
    APP_STATE.htmlScrollListener = () => {
        if (APP_STATE.syncInProgress) return; // Prevent circular updates
        
        APP_STATE.syncInProgress = true;
        
        // Find which element with page number is currently most visible
        const elementsWithPages = element.querySelectorAll('[data-page]');
        let currentPageNum = 1;
        
        if (elementsWithPages.length > 0) {
            const containerRect = element.getBoundingClientRect();
            const viewportMiddle = containerRect.top + containerRect.height / 2;
            
            let minDistance = Infinity;
            
            elementsWithPages.forEach(el => {
                const rect = el.getBoundingClientRect();
                const elementMiddle = rect.top + rect.height / 2;
                const distance = Math.abs(elementMiddle - viewportMiddle);
                
                if (distance < minDistance) {
                    minDistance = distance;
                    const pageAttr = el.getAttribute('data-page');
                    if (pageAttr) {
                        currentPageNum = parseInt(pageAttr);
                    }
                }
            });
            
            // Sync PDF to this page
            if (currentPageNum > 0 && currentPageNum <= APP_STATE.totalPages) {
                syncPdfToPage(currentPageNum);
                syncXmlToPage(currentPageNum);
            }
        } else {
            // Fallback to percentage-based sync
            const scrollPercentage = element.scrollTop / (element.scrollHeight - element.clientHeight);
            syncPdfToPercentage(scrollPercentage);
            syncXmlToPercentage(scrollPercentage);
        }
        
        setTimeout(() => {
            APP_STATE.syncInProgress = false;
        }, 100);
    };
    
    element.addEventListener('scroll', APP_STATE.htmlScrollListener);
}

// Setup XML scroll sync
function setupXmlScrollSync() {
    if (!APP_STATE.monacoEditor) {
        return;
    }
    
    if (APP_STATE.xmlScrollDisposable) {
        APP_STATE.xmlScrollDisposable.dispose();
    }
    
    APP_STATE.xmlScrollDisposable = APP_STATE.monacoEditor.onDidScrollChange(() => {
        if (APP_STATE.syncInProgress) {
            return;
        }
        
        APP_STATE.syncInProgress = true;
        
        const editor = APP_STATE.monacoEditor;
        const scrollTop = editor.getScrollTop();
        const maxScroll = Math.max(editor.getScrollHeight() - editor.getLayoutInfo().height, 1);
        const percentage = Math.max(0, Math.min(1, scrollTop / maxScroll));
        
        syncPdfToPercentage(percentage);
        syncHtmlToPercentage(percentage);
        
        setTimeout(() => {
            APP_STATE.syncInProgress = false;
        }, 100);
    });
}

// Sync PDF to specific page number
function syncPdfToPage(pageNum) {
    const pageWrapper = document.querySelector(`.pdf-page-wrapper[data-page="${pageNum}"]`);
    if (pageWrapper) {
        const pdfViewer = document.getElementById('pdfViewer');
        const containerRect = pdfViewer.getBoundingClientRect();
        const pageRect = pageWrapper.getBoundingClientRect();
        const relativeTop = pageRect.top - containerRect.top + pdfViewer.scrollTop;
        
        pdfViewer.scrollTo({
            top: relativeTop,
            behavior: 'smooth'
        });
    }
}

// Sync PDF to scroll percentage
function syncPdfToPercentage(percentage) {
    const pdfViewer = document.getElementById('pdfViewer');
    if (pdfViewer) {
        const targetScroll = percentage * (pdfViewer.scrollHeight - pdfViewer.clientHeight);
        pdfViewer.scrollTop = targetScroll;
    }
}

// Sync XML editor to scroll percentage
function syncXmlToPercentage(percentage) {
    if (APP_STATE.monacoEditor) {
        const editor = APP_STATE.monacoEditor;
        const lineCount = editor.getModel().getLineCount();
        const targetLine = Math.floor(percentage * lineCount);
        editor.revealLineInCenter(Math.max(1, targetLine));
    }
}

// Sync HTML to scroll percentage
function syncHtmlToPercentage(percentage) {
    const htmlContainer = APP_STATE.currentView === 'html' 
        ? document.getElementById('htmlPreview')
        : document.getElementById('htmlEditableContent');
    
    if (htmlContainer && htmlContainer.style.display !== 'none') {
        const targetScroll = percentage * (htmlContainer.scrollHeight - htmlContainer.clientHeight);
        htmlContainer.scrollTop = targetScroll;
    }
}

// Lightweight function to update page number from scroll (no sync operations)
function updatePageNumberFromScroll() {
    const pdfViewer = document.getElementById('pdfViewer');
    const pages = document.querySelectorAll('.pdf-page-wrapper');

    if (pages.length === 0) return;

    const viewerRect = pdfViewer.getBoundingClientRect();
    const viewerMidpoint = viewerRect.top + viewerRect.height / 2;

    let currentPageNum = 1;
    let minDistance = Infinity;

    // Find the page whose center is closest to the viewport center
    pages.forEach(pageWrapper => {
        const pageNum = parseInt(pageWrapper.getAttribute('data-page'));
        const rect = pageWrapper.getBoundingClientRect();
        const pageMidpoint = rect.top + rect.height / 2;
        const distance = Math.abs(pageMidpoint - viewerMidpoint);

        if (distance < minDistance) {
            minDistance = distance;
            currentPageNum = pageNum;
        }
    });

    // Update UI only if page changed
    if (currentPageNum !== APP_STATE.currentPage) {
        APP_STATE.currentPage = currentPageNum;
        document.getElementById('pageNumberInput').value = currentPageNum;
        updateThumbnailSelection(currentPageNum);
    }
}

// Handle PDF scroll (includes sync operations)
function handlePdfScroll() {
    if (APP_STATE.syncInProgress) return; // Prevent circular updates

    APP_STATE.syncInProgress = true;

    const pdfViewer = document.getElementById('pdfViewer');
    const pages = document.querySelectorAll('.pdf-page-wrapper');

    if (pages.length === 0) {
        APP_STATE.syncInProgress = false;
        return;
    }

    // Use the current page from state (already updated by updatePageNumberFromScroll)
    const currentPageNum = APP_STATE.currentPage;

    // Sync HTML view to this page number (smarter sync)
    syncHtmlToPage(currentPageNum);

    // Sync XML editor to this page number
    syncXmlToPage(currentPageNum);

    setTimeout(() => {
        APP_STATE.syncInProgress = false;
    }, 100);
}

// Sync HTML to specific page number (content-aware)
function syncHtmlToPage(pageNum) {
    const htmlContainer = APP_STATE.currentView === 'html'
        ? document.getElementById('htmlPreview')
        : document.getElementById('htmlEditableContent');

    if (!htmlContainer || htmlContainer.style.display === 'none') {
        return;
    }

    // Find element with matching page number using improved helper
    const pageElement = getFirstElementForPage(htmlContainer, pageNum);

    if (pageElement) {
        // Scroll to the element
        const containerRect = htmlContainer.getBoundingClientRect();
        const elementRect = pageElement.getBoundingClientRect();
        const relativeTop = elementRect.top - containerRect.top + htmlContainer.scrollTop;

        // Smooth scroll to element
        htmlContainer.scrollTo({
            top: relativeTop - 50, // 50px offset from top
            behavior: 'smooth'
        });

        // Highlight the synced element briefly
        highlightSyncedElement(pageElement);
    } else {
        // Fallback to percentage-based sync if no page data
        const scrollPercentage = (pageNum - 1) / Math.max(APP_STATE.totalPages - 1, 1);
        const targetScroll = scrollPercentage * (htmlContainer.scrollHeight - htmlContainer.clientHeight);
        htmlContainer.scrollTop = targetScroll;
    }
}

// Highlight a synced element briefly
function highlightSyncedElement(element) {
    element.classList.add('sync-highlight');
    setTimeout(() => {
        element.classList.remove('sync-highlight');
    }, 1000);
}

// Sync XML editor to specific page number
function syncXmlToPage(pageNum) {
    if (!APP_STATE.monacoEditor) {
        return;
    }
    
    const editor = APP_STATE.monacoEditor;
    const model = editor.getModel();
    const content = model.getValue();
    
    // Search for page reference in XML
    const pagePattern = new RegExp(`page=["']${pageNum}["']`, 'i');
    const lines = content.split('\n');
    
    for (let i = 0; i < lines.length; i++) {
        if (pagePattern.test(lines[i])) {
            // Found a line with this page number, scroll to it
            editor.revealLineInCenter(i + 1);
            return;
        }
    }
    
    // Fallback to percentage-based sync
    const lineCount = model.getLineCount();
    const scrollPercentage = (pageNum - 1) / Math.max(APP_STATE.totalPages - 1, 1);
    const targetLine = Math.floor(scrollPercentage * lineCount);
    editor.revealLineInCenter(Math.max(1, targetLine));
}

// Scroll to specific page (smooth scroll)
function scrollToPageSmooth(pageNum) {
    const pageWrapper = document.querySelector(`.pdf-page-wrapper[data-page="${pageNum}"]`);
    if (pageWrapper) {
        pageWrapper.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
}

// Scroll to specific page (instant scroll to avoid sync issues)
function scrollToPage(pageNum) {
    APP_STATE.syncInProgress = true;
    
    const pageWrapper = document.querySelector(`.pdf-page-wrapper[data-page="${pageNum}"]`);
    if (pageWrapper) {
        pageWrapper.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    
    setTimeout(() => {
        APP_STATE.syncInProgress = false;
    }, 500);
}

// Setup page mapping
async function setupPageMapping() {
    try {
        const response = await fetch('/api/page-mapping');
        const data = await response.json();

        if (data.mapping) {
            APP_STATE.pageToXmlMapping = data.mapping;
            APP_STATE.xmlToPageMapping = data.element_to_page || {};
            APP_STATE.totalXmlPages = data.total_xml_pages || 0;
            console.log(`Page mapping loaded: ${data.page_count} pages with content`);
        }
    } catch (error) {
        console.log('Page mapping not available, using estimated mapping');
        // Create estimated mapping based on page count
        for (let i = 1; i <= APP_STATE.totalPages; i++) {
            APP_STATE.pageToXmlMapping[i] = [];
        }
    }
}

// Get the first element for a given page number from the HTML
function getFirstElementForPage(container, pageNum) {
    // Try exact match first
    let element = container.querySelector(`[data-page="${pageNum}"]`);
    if (element) return element;

    // Try finding any element with data-page attribute
    const allElements = container.querySelectorAll('[data-page]');
    let closest = null;
    let closestDiff = Infinity;

    allElements.forEach(el => {
        const elPage = parseInt(el.getAttribute('data-page'));
        if (!isNaN(elPage)) {
            const diff = Math.abs(elPage - pageNum);
            if (diff < closestDiff) {
                closestDiff = diff;
                closest = el;
            }
        }
    });

    return closest;
}

// Find all elements for a specific page
function getElementsForPage(container, pageNum) {
    return container.querySelectorAll(`[data-page="${pageNum}"]`);
}

// Save changes
async function saveChanges(reprocess) {
    try {
        showLoading();
        
        let content = '';
        let contentType = 'xml';
        
        if (APP_STATE.currentView === 'xml') {
            content = APP_STATE.monacoEditor.getValue();
            contentType = 'xml';
        } else if (APP_STATE.currentView === 'htmledit') {
            // Get HTML content from editable div
            const editableContent = document.getElementById('htmlEditableContent');
            content = editableContent.innerHTML;
            contentType = 'html';
        }
        
        const response = await fetch('/api/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                type: contentType,
                content: content,
                reprocess: reprocess
            })
        });
        
        const result = await response.json();
        
        hideLoading();
        
        if (result.error) {
            showNotification(result.error, 'error');
        } else if (result.success) {
            showNotification(result.message || 'Saved successfully!', 'success');
            APP_STATE.isHtmlEdited = false;
            
            if (result.package) {
                showNotification(`Package created: ${result.package}`, 'success');
            }
            
            // Refresh HTML content if we saved from HTML edit mode
            if (contentType === 'html' && result.html) {
                APP_STATE.htmlContent = result.html;
            }
        }
    } catch (error) {
        console.error('Error saving:', error);
        showNotification('Failed to save changes', 'error');
        hideLoading();
    }
}

// Load PDF
async function loadPDF() {
    try {
        const loadingTask = pdfjsLib.getDocument('/api/pdf');
        APP_STATE.pdfDoc = await loadingTask.promise;
        APP_STATE.totalPages = APP_STATE.pdfDoc.numPages;
        
        document.getElementById('totalPages').textContent = APP_STATE.totalPages;
        
        // Render all pages in continuous scroll
        await renderAllPages();
        
        // Generate thumbnails
        await generateThumbnails();
        
        console.log(`PDF loaded: ${APP_STATE.totalPages} pages`);
    } catch (error) {
        console.error('Error loading PDF:', error);
        showNotification('Failed to load PDF', 'error');
    }
}

// Render all pages for continuous scrolling
async function renderAllPages() {
    const container = document.getElementById('pdfPagesContainer');
    container.innerHTML = '';
    APP_STATE.pageElements = [];

    for (let i = 1; i <= APP_STATE.totalPages; i++) {
        const pageWrapper = document.createElement('div');
        pageWrapper.className = 'pdf-page-wrapper';
        pageWrapper.setAttribute('data-page', i);

        // Canvas container for positioning
        const canvasContainer = document.createElement('div');
        canvasContainer.className = 'pdf-canvas-container';

        const canvas = document.createElement('canvas');
        canvas.id = `pdfCanvas-${i}`;

        // Text layer for selectable text
        const textLayer = document.createElement('div');
        textLayer.className = 'textLayer';
        textLayer.id = `textLayer-${i}`;

        canvasContainer.appendChild(canvas);
        canvasContainer.appendChild(textLayer);

        const pageNumberLabel = document.createElement('div');
        pageNumberLabel.className = 'pdf-page-number';
        pageNumberLabel.textContent = `Page ${i}`;

        const overlay = document.createElement('div');
        overlay.className = 'screenshot-overlay';
        overlay.id = `screenshotOverlay-${i}`;

        pageWrapper.appendChild(canvasContainer);
        pageWrapper.appendChild(pageNumberLabel);
        pageWrapper.appendChild(overlay);

        container.appendChild(pageWrapper);

        // Render the page with text layer
        await renderPage(i, canvas, textLayer);

        APP_STATE.pageElements.push(pageWrapper);
    }
}

// Render PDF page with text layer for selectable text
async function renderPage(pageNum, canvas, textLayerDiv) {
    try {
        const page = await APP_STATE.pdfDoc.getPage(pageNum);
        const context = canvas.getContext('2d');

        const viewport = page.getViewport({ scale: APP_STATE.scale });
        canvas.height = viewport.height;
        canvas.width = viewport.width;

        const renderContext = {
            canvasContext: context,
            viewport: viewport
        };

        await page.render(renderContext).promise;

        // Render text layer for selectable text
        if (textLayerDiv) {
            textLayerDiv.innerHTML = '';
            textLayerDiv.style.width = `${viewport.width}px`;
            textLayerDiv.style.height = `${viewport.height}px`;

            // Get text content from the page
            const textContent = await page.getTextContent();

            // Use PDF.js TextLayer API
            pdfjsLib.renderTextLayer({
                textContentSource: textContent,
                container: textLayerDiv,
                viewport: viewport,
                textDivs: []
            });
        }

    } catch (error) {
        console.error(`Error rendering page ${pageNum}:`, error);
    }
}

// Generate thumbnails
async function generateThumbnails() {
    const thumbnailList = document.getElementById('thumbnailList');
    thumbnailList.innerHTML = '';
    
    for (let i = 1; i <= APP_STATE.totalPages; i++) {
        const thumbnailItem = document.createElement('div');
        thumbnailItem.className = 'thumbnail-item';
        thumbnailItem.dataset.page = i;
        
        const canvas = document.createElement('canvas');
        const page = await APP_STATE.pdfDoc.getPage(i);
        const viewport = page.getViewport({ scale: 0.2 });
        
        canvas.height = viewport.height;
        canvas.width = viewport.width;
        
        const context = canvas.getContext('2d');
        await page.render({ canvasContext: context, viewport: viewport }).promise;
        
        thumbnailItem.appendChild(canvas);
        
        const pageNum = document.createElement('div');
        pageNum.className = 'thumbnail-page-num';
        pageNum.textContent = `${i}`;
        thumbnailItem.appendChild(pageNum);
        
        thumbnailItem.addEventListener('click', () => {
            scrollToPage(i);
        });
        
        thumbnailList.appendChild(thumbnailItem);
    }
    
    // Update initial selection
    updateThumbnailSelection(1);
}

// Update thumbnail selection
function updateThumbnailSelection(pageNum) {
    document.querySelectorAll('.thumbnail-item').forEach(item => {
        item.classList.remove('active');
        if (parseInt(item.dataset.page) === pageNum) {
            item.classList.add('active');
            // Scroll thumbnail into view
            item.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
        }
    });
}

// Change page
function changePage(delta) {
    const newPage = APP_STATE.currentPage + delta;
    if (newPage >= 1 && newPage <= APP_STATE.totalPages) {
        scrollToPage(newPage);
    }
}

// Change zoom
async function changeZoom(delta) {
    APP_STATE.scale = Math.max(0.5, Math.min(3.0, APP_STATE.scale + delta));
    document.getElementById('zoomLevel').textContent = `${Math.round(APP_STATE.scale * 100)}%`;
    
    // Re-render all pages with new scale
    await renderAllPages();
    
    // Scroll back to current page
    setTimeout(() => {
        scrollToPage(APP_STATE.currentPage);
    }, 100);
}

// Toggle screenshot mode
function toggleScreenshotMode() {
    APP_STATE.screenshotMode = !APP_STATE.screenshotMode;
    const btn = document.getElementById('screenshotBtn');

    if (APP_STATE.screenshotMode) {
        btn.style.background = '#ef4444';
        btn.style.color = 'white';
        btn.title = 'Click to exit screenshot mode';

        // Add screenshot mode to all page wrappers
        document.querySelectorAll('.pdf-page-wrapper').forEach(wrapper => {
            wrapper.classList.add('screenshot-mode');
            const overlay = wrapper.querySelector('.screenshot-overlay');
            if (overlay) {
                // Clear existing event listeners by cloning and replacing
                const newOverlay = overlay.cloneNode(true);
                overlay.parentNode.replaceChild(newOverlay, overlay);
                setupScreenshotCapture(newOverlay, wrapper);
            }
        });

        showNotification('Screenshot mode ON - Draw a rectangle on the PDF to capture', 'success');
    } else {
        btn.style.background = '';
        btn.style.color = '';
        btn.title = 'Screenshot Mode';

        // Remove screenshot mode
        document.querySelectorAll('.pdf-page-wrapper').forEach(wrapper => {
            wrapper.classList.remove('screenshot-mode');
        });

        showNotification('Screenshot mode OFF', 'warning');
    }
}

// Setup screenshot capture
function setupScreenshotCapture(overlay, pageWrapper) {
    const canvas = pageWrapper.querySelector('canvas');
    const pageNum = parseInt(pageWrapper.getAttribute('data-page'));
    
    let isDrawing = false;
    let startX, startY;
    let selectionDiv = null;
    
    overlay.addEventListener('mousedown', (e) => {
        isDrawing = true;
        const rect = canvas.getBoundingClientRect();
        startX = e.clientX - rect.left;
        startY = e.clientY - rect.top;
        
        selectionDiv = document.createElement('div');
        selectionDiv.className = 'screenshot-selection';
        selectionDiv.style.left = startX + 'px';
        selectionDiv.style.top = startY + 'px';
        overlay.appendChild(selectionDiv);
    });
    
    overlay.addEventListener('mousemove', (e) => {
        if (!isDrawing || !selectionDiv) return;
        
        const rect = canvas.getBoundingClientRect();
        const currentX = e.clientX - rect.left;
        const currentY = e.clientY - rect.top;
        
        const width = currentX - startX;
        const height = currentY - startY;
        
        selectionDiv.style.width = Math.abs(width) + 'px';
        selectionDiv.style.height = Math.abs(height) + 'px';
        selectionDiv.style.left = (width < 0 ? currentX : startX) + 'px';
        selectionDiv.style.top = (height < 0 ? currentY : startY) + 'px';
    });
    
    overlay.addEventListener('mouseup', (e) => {
        if (!isDrawing || !selectionDiv) return;
        
        isDrawing = false;
        
        const rect = canvas.getBoundingClientRect();
        const endX = e.clientX - rect.left;
        const endY = e.clientY - rect.top;
        
        // Capture screenshot
        captureScreenshot(
            canvas,
            Math.min(startX, endX),
            Math.min(startY, endY),
            Math.abs(endX - startX),
            Math.abs(endY - startY),
            pageNum
        );
        
        overlay.removeChild(selectionDiv);
        selectionDiv = null;
    });
}

// Capture screenshot
function captureScreenshot(sourceCanvas, x, y, width, height, pageNum) {
    // Minimum size validation
    if (width < 10 || height < 10) {
        showNotification('Selection too small. Please draw a larger rectangle.', 'warning');
        return;
    }

    // Ensure coordinates are within canvas bounds
    const canvasWidth = sourceCanvas.width;
    const canvasHeight = sourceCanvas.height;

    x = Math.max(0, Math.min(x, canvasWidth));
    y = Math.max(0, Math.min(y, canvasHeight));
    width = Math.min(width, canvasWidth - x);
    height = Math.min(height, canvasHeight - y);

    const screenshotCanvas = document.createElement('canvas');
    screenshotCanvas.width = width;
    screenshotCanvas.height = height;

    const ctx = screenshotCanvas.getContext('2d');
    ctx.drawImage(sourceCanvas, x, y, width, height, 0, 0, width, height);

    const imageData = screenshotCanvas.toDataURL('image/png');
    APP_STATE.screenshotData = imageData;
    APP_STATE.screenshotPage = pageNum;

    // Show dialog
    showScreenshotDialog(imageData, pageNum);
}

// Show screenshot dialog
function showScreenshotDialog(imageData, pageNum) {
    const dialog = document.getElementById('screenshotDialog');
    const preview = document.getElementById('screenshotPreview');
    const select = document.getElementById('replaceImageSelect');

    preview.src = imageData;

    // Populate image list from media files
    select.innerHTML = '<option value="">-- Save as new image --</option>';

    // Add images from media files
    APP_STATE.mediaFiles.forEach(file => {
        const option = document.createElement('option');
        option.value = file.name;
        option.textContent = file.name;
        select.appendChild(option);
    });

    // Also scan HTML content for image references
    const htmlContainer = document.getElementById('htmlPreview') || document.getElementById('htmlEditableContent');
    if (htmlContainer) {
        const images = htmlContainer.querySelectorAll('img');
        const existingOptions = new Set(Array.from(select.options).map(o => o.value));

        images.forEach(img => {
            const src = img.getAttribute('src') || '';
            // Extract filename from path
            const filename = src.split('/').pop();
            if (filename && !existingOptions.has(filename)) {
                const option = document.createElement('option');
                option.value = filename;
                option.textContent = `${filename} (from document)`;
                select.appendChild(option);
                existingOptions.add(filename);
            }
        });
    }

    // Generate filename
    const timestamp = Date.now();
    document.getElementById('screenshotFilename').value = `screenshot_page${pageNum}_${timestamp}.png`;

    dialog.style.display = 'flex';

    showNotification('Screenshot captured! Choose to save as new image or replace an existing one.', 'success');
}

// Close screenshot dialog
function closeScreenshotDialog() {
    document.getElementById('screenshotDialog').style.display = 'none';
    if (APP_STATE.screenshotMode) {
        toggleScreenshotMode();
    }
}

// Show save confirmation dialog
function showSaveConfirmDialog() {
    document.getElementById('saveConfirmDialog').style.display = 'flex';
}

// Close save confirmation dialog
function closeSaveConfirmDialog() {
    document.getElementById('saveConfirmDialog').style.display = 'none';
}

// Confirm save and process
function confirmSaveAndProcess() {
    closeSaveConfirmDialog();
    saveChanges(true);  // Always run full finalization
}

// Save screenshot
async function saveScreenshot() {
    const filename = document.getElementById('screenshotFilename').value;
    const replaceImage = document.getElementById('replaceImageSelect').value;

    const targetFilename = replaceImage || filename;

    if (!targetFilename || targetFilename.trim() === '') {
        showNotification('Please enter a filename or select an image to replace', 'error');
        return;
    }

    try {
        showLoading();

        const response = await fetch('/api/screenshot', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                imageData: APP_STATE.screenshotData,
                targetFilename: targetFilename,
                pageNumber: APP_STATE.screenshotPage,
                replaceExisting: !!replaceImage
            })
        });

        const result = await response.json();

        hideLoading();
        closeScreenshotDialog();

        if (result.error) {
            showNotification(result.error, 'error');
        } else {
            showNotification(result.message || `Screenshot saved as ${targetFilename}`, 'success');
            await loadMediaFiles();

            // If replacing an existing image, refresh the HTML preview to show the new image
            if (replaceImage) {
                refreshImagesInPreview(replaceImage);
            }
        }
    } catch (error) {
        console.error('Error saving screenshot:', error);
        showNotification('Failed to save screenshot', 'error');
        hideLoading();
    }
}

// Refresh images in the preview that match a specific filename
function refreshImagesInPreview(filename) {
    const timestamp = Date.now();

    // Refresh in HTML preview
    const htmlPreview = document.getElementById('htmlPreview');
    if (htmlPreview) {
        const images = htmlPreview.querySelectorAll('img');
        images.forEach(img => {
            const src = img.getAttribute('src') || '';
            if (src.includes(filename)) {
                // Add timestamp to force reload
                const newSrc = src.split('?')[0] + '?t=' + timestamp;
                img.setAttribute('src', newSrc);
            }
        });
    }

    // Refresh in HTML editable content
    const editableContent = document.getElementById('htmlEditableContent');
    if (editableContent) {
        const images = editableContent.querySelectorAll('img');
        images.forEach(img => {
            const src = img.getAttribute('src') || '';
            if (src.includes(filename)) {
                const newSrc = src.split('?')[0] + '?t=' + timestamp;
                img.setAttribute('src', newSrc);
            }
        });
    }

    showNotification(`Image ${filename} updated in preview`, 'success');
}

// Load media files
async function loadMediaFiles() {
    try {
        const response = await fetch('/api/media-list');
        const data = await response.json();
        
        if (data.files) {
            APP_STATE.mediaFiles = data.files;
        }
    } catch (error) {
        console.error('Error loading media files:', error);
    }
}

// Format XML
function formatXML() {
    try {
        const xml = APP_STATE.monacoEditor.getValue();
        const formatted = formatXMLString(xml);
        APP_STATE.monacoEditor.setValue(formatted);
        showNotification('XML formatted successfully', 'success');
    } catch (error) {
        showNotification('Error formatting XML: ' + error.message, 'error');
    }
}

// Format XML string
function formatXMLString(xml) {
    const PADDING = '  ';
    const reg = /(>)(<)(\/*)/g;
    let pad = 0;
    
    xml = xml.replace(reg, '$1\n$2$3');
    
    return xml.split('\n').map((node) => {
        let indent = 0;
        if (node.match(/.+<\/\w[^>]*>$/)) {
            indent = 0;
        } else if (node.match(/^<\/\w/) && pad > 0) {
            pad -= 1;
        } else if (node.match(/^<\w[^>]*[^\/]>.*$/)) {
            indent = 1;
        } else {
            indent = 0;
        }
        
        const padding = PADDING.repeat(pad);
        pad += indent;
        
        return padding + node;
    }).join('\n');
}

// Validate XML
async function validateXML() {
    try {
        const xml = APP_STATE.monacoEditor.getValue();
        
        // Basic XML syntax validation
        const parser = new DOMParser();
        const xmlDoc = parser.parseFromString(xml, 'text/xml');
        const parserError = xmlDoc.querySelector('parsererror');
        
        if (parserError) {
            showNotification('XML validation failed: Invalid syntax', 'error');
        } else {
            showNotification('XML is well-formed', 'success');
        }
    } catch (error) {
        showNotification('Error validating XML: ' + error.message, 'error');
    }
}

// Refresh HTML preview
async function refreshHTMLPreview() {
    try {
        showLoading();
        
        const xml = APP_STATE.monacoEditor.getValue();
        
        const response = await fetch('/api/render-html', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ xml: xml })
        });
        
        const result = await response.json();
        
        hideLoading();
        
        if (result.error) {
            showNotification(result.error, 'error');
        } else {
            APP_STATE.htmlContent = result.html;
            const enhanced = enhanceHTMLContent(result.html);
            document.getElementById('htmlPreview').innerHTML = enhanced;
            showNotification('HTML preview refreshed', 'success');
        }
    } catch (error) {
        console.error('Error refreshing HTML:', error);
        showNotification('Failed to refresh HTML preview', 'error');
        hideLoading();
    }
}

// Toggle thumbnails
function toggleThumbnails() {
    const thumbnailBar = document.getElementById('thumbnailBar');
    const btn = document.getElementById('toggleThumbnailsBtn');
    
    thumbnailBar.classList.toggle('collapsed');
    
    if (thumbnailBar.classList.contains('collapsed')) {
        btn.innerHTML = '<i class="fas fa-chevron-up"></i> Pages';
    } else {
        btn.innerHTML = '<i class="fas fa-chevron-down"></i> Pages';
    }
}

// Setup resizer
function setupResizer() {
    const resizer = document.getElementById('resizer');
    const leftPanel = document.querySelector('.left-panel');
    const rightPanel = document.querySelector('.right-panel');
    
    let isResizing = false;
    
    resizer.addEventListener('mousedown', (e) => {
        isResizing = true;
        document.body.style.cursor = 'col-resize';
    });
    
    document.addEventListener('mousemove', (e) => {
        if (!isResizing) return;
        
        const containerWidth = document.querySelector('.main-container').offsetWidth;
        const leftWidth = (e.clientX / containerWidth) * 100;
        
        if (leftWidth > 20 && leftWidth < 80) {
            leftPanel.style.width = leftWidth + '%';
            rightPanel.style.width = (100 - leftWidth) + '%';
        }
    });
    
    document.addEventListener('mouseup', () => {
        isResizing = false;
        document.body.style.cursor = '';
    });
}

// Show loading spinner
function showLoading() {
    document.getElementById('loadingSpinner').style.display = 'flex';
}

// Hide loading spinner
function hideLoading() {
    document.getElementById('loadingSpinner').style.display = 'none';
}

// Show notification
function showNotification(message, type = 'success') {
    const toast = document.getElementById('notificationToast');
    toast.textContent = message;
    toast.className = `notification-toast ${type} show`;
    
    setTimeout(() => {
        toast.classList.remove('show');
    }, 3000);
}

// Setup Rich Text Toolbar
function setupRichTextToolbar() {
    // Handle toolbar button clicks
    document.querySelectorAll('.toolbar-btn[data-command]').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            const command = btn.getAttribute('data-command');
            document.execCommand(command, false, null);
            document.getElementById('htmlEditableContent').focus();
        });
    });
    
    // Font size selector
    const fontSizeSelect = document.getElementById('fontSizeSelect');
    if (fontSizeSelect) {
        fontSizeSelect.addEventListener('change', (e) => {
            const size = e.target.value;
            if (size) {
                document.execCommand('fontSize', false, size);
                document.getElementById('htmlEditableContent').focus();
            }
            e.target.value = ''; // Reset select
        });
    }
    
    // Font name selector
    const fontNameSelect = document.getElementById('fontNameSelect');
    if (fontNameSelect) {
        fontNameSelect.addEventListener('change', (e) => {
            const font = e.target.value;
            if (font) {
                document.execCommand('fontName', false, font);
                document.getElementById('htmlEditableContent').focus();
            }
            e.target.value = ''; // Reset select
        });
    }
    
    // Text color picker
    const textColorPicker = document.getElementById('textColorPicker');
    if (textColorPicker) {
        textColorPicker.addEventListener('change', (e) => {
            document.execCommand('foreColor', false, e.target.value);
            document.getElementById('htmlEditableContent').focus();
        });
    }
    
    // Background color picker
    const bgColorPicker = document.getElementById('bgColorPicker');
    if (bgColorPicker) {
        bgColorPicker.addEventListener('change', (e) => {
            document.execCommand('backColor', false, e.target.value);
            document.getElementById('htmlEditableContent').focus();
        });
    }
}

// Insert Link
function insertLink() {
    const url = prompt('Enter URL:', 'https://');
    if (url && url.trim() !== '' && url !== 'https://') {
        document.execCommand('createLink', false, url);
        document.getElementById('htmlEditableContent').focus();
    }
}

// Insert Table
function insertTable() {
    const rows = prompt('Number of rows:', '3');
    const cols = prompt('Number of columns:', '3');
    
    if (rows && cols) {
        const numRows = parseInt(rows);
        const numCols = parseInt(cols);
        
        if (numRows > 0 && numCols > 0) {
            let tableHTML = '<table border="1" style="border-collapse: collapse; width: 100%; margin: 1rem 0;"><tbody>';
            
            for (let i = 0; i < numRows; i++) {
                tableHTML += '<tr>';
                for (let j = 0; j < numCols; j++) {
                    tableHTML += '<td style="border: 1px solid #e2e8f0; padding: 0.5rem;">&nbsp;</td>';
                }
                tableHTML += '</tr>';
            }
            
            tableHTML += '</tbody></table>';
            
            document.execCommand('insertHTML', false, tableHTML);
            document.getElementById('htmlEditableContent').focus();
        }
    }
}

// Show Math Symbol Picker
function showMathSymbolPicker() {
    document.getElementById('mathSymbolDialog').style.display = 'flex';
}

// Close Math Symbol Dialog
function closeMathSymbolDialog() {
    document.getElementById('mathSymbolDialog').style.display = 'none';
}

// Insert Symbol
function insertSymbol(symbol) {
    const editableContent = document.getElementById('htmlEditableContent');
    editableContent.focus();
    document.execCommand('insertText', false, symbol);
    closeMathSymbolDialog();
}

// Toggle block info overlay
function toggleBlockOverlay() {
    APP_STATE.showBlockOverlay = !APP_STATE.showBlockOverlay;
    const btn = document.getElementById('blockOverlayBtn');

    if (APP_STATE.showBlockOverlay) {
        btn.classList.add('active');
        btn.style.background = '#3b82f6';
        btn.style.color = 'white';
        document.body.classList.add('show-block-overlay');
        applyBlockOverlays();
    } else {
        btn.classList.remove('active');
        btn.style.background = '';
        btn.style.color = '';
        document.body.classList.remove('show-block-overlay');
        removeBlockOverlays();
    }
}

// Apply block overlays to HTML content
function applyBlockOverlays() {
    const htmlContainer = APP_STATE.currentView === 'html'
        ? document.getElementById('htmlPreview')
        : document.getElementById('htmlEditableContent');

    if (!htmlContainer) return;

    const isEditMode = APP_STATE.currentView === 'htmledit';

    // Find all content blocks with data attributes
    const blocks = htmlContainer.querySelectorAll('[data-page], [data-reading-block], [data-reading-order], [data-col-id]');

    blocks.forEach((block, index) => {
        // Remove existing overlay if any
        const existingOverlay = block.querySelector('.block-info-overlay');
        if (existingOverlay) {
            existingOverlay.remove();
        }

        // Get block info
        const page = block.getAttribute('data-page') || '-';
        const readingBlock = block.getAttribute('data-reading-block') || '-';
        const readingOrder = block.getAttribute('data-reading-order') || '-';
        const colId = block.getAttribute('data-col-id') || '-';

        // Only show overlay if at least one value exists
        if (page !== '-' || readingBlock !== '-' || readingOrder !== '-' || colId !== '-') {
            // Create overlay element
            const overlay = document.createElement('div');
            overlay.className = 'block-info-overlay';

            // In edit mode, make B: and C: editable
            if (isEditMode) {
                overlay.innerHTML = `
                    <span class="block-info-item page" title="Page">P:${page}</span>
                    <span class="block-info-item block editable" title="Click to edit Reading Block" data-field="reading-block" data-element-id="${index}">B:<span class="editable-value">${readingBlock}</span></span>
                    <span class="block-info-item order" title="Reading Order">O:${readingOrder}</span>
                    <span class="block-info-item col editable" title="Click to edit Column ID" data-field="col-id" data-element-id="${index}">C:<span class="editable-value">${colId}</span></span>
                `;
                // Store reference to the block element
                block.setAttribute('data-block-index', index);
            } else {
                overlay.innerHTML = `
                    <span class="block-info-item page" title="Page">P:${page}</span>
                    <span class="block-info-item block" title="Reading Block">B:${readingBlock}</span>
                    <span class="block-info-item order" title="Reading Order">O:${readingOrder}</span>
                    <span class="block-info-item col" title="Column ID">C:${colId}</span>
                `;
            }

            // Make the block position relative for overlay positioning
            const computedStyle = window.getComputedStyle(block);
            if (computedStyle.position === 'static') {
                block.style.position = 'relative';
            }

            block.insertBefore(overlay, block.firstChild);

            // Add click handlers for editable items in edit mode
            if (isEditMode) {
                const editableItems = overlay.querySelectorAll('.block-info-item.editable');
                editableItems.forEach(item => {
                    item.addEventListener('click', (e) => {
                        e.stopPropagation();
                        startInlineEdit(item, block);
                    });
                });
            }

            // Add border color based on reading block for visual grouping
            if (readingBlock !== '-') {
                const blockNum = parseInt(readingBlock);
                const colors = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899', '#06b6d4'];
                const color = colors[blockNum % colors.length];
                block.style.borderLeft = `4px solid ${color}`;
            }
        }
    });
}

// Start inline editing of a block info value
function startInlineEdit(item, blockElement) {
    // Check if already editing
    if (item.querySelector('input')) return;

    const field = item.getAttribute('data-field');
    const valueSpan = item.querySelector('.editable-value');
    const currentValue = valueSpan.textContent;

    // Create input element
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'inline-edit-input';
    input.value = currentValue === '-' ? '' : currentValue;
    input.setAttribute('data-original-value', currentValue);
    input.size = 3;

    // Replace the value span with input
    valueSpan.style.display = 'none';
    item.appendChild(input);
    input.focus();
    input.select();

    // Handle input events
    input.addEventListener('blur', () => finishInlineEdit(input, item, blockElement, field));
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            input.blur();
        } else if (e.key === 'Escape') {
            e.preventDefault();
            input.value = input.getAttribute('data-original-value');
            input.blur();
        }
    });
}

// Finish inline editing and apply changes
function finishInlineEdit(input, item, blockElement, field) {
    const newValue = input.value.trim() || '-';
    const originalValue = input.getAttribute('data-original-value');
    const valueSpan = item.querySelector('.editable-value');

    // Remove input and show value span
    input.remove();
    valueSpan.style.display = '';
    valueSpan.textContent = newValue;

    // If value changed, update the data attribute and handle block changes
    if (newValue !== originalValue) {
        const attrName = `data-${field}`;

        if (field === 'reading-block' && newValue !== '-' && newValue !== originalValue) {
            // Handle block number change - merge with target block
            handleBlockNumberChange(blockElement, originalValue, newValue);
        } else {
            // Just update the attribute for column ID changes
            blockElement.setAttribute(attrName, newValue);
            APP_STATE.isHtmlEdited = true;
            showNotification(`Updated ${field} to ${newValue}`, 'success');
        }
    }
}

// Handle block number change - merge content with target block
function handleBlockNumberChange(sourceElement, oldBlockNum, newBlockNum) {
    const htmlContainer = document.getElementById('htmlEditableContent');
    if (!htmlContainer) return;

    const page = sourceElement.getAttribute('data-page');

    // Find all elements in the target block (same page, same block number)
    const targetBlocks = htmlContainer.querySelectorAll(
        `[data-page="${page}"][data-reading-block="${newBlockNum}"]`
    );

    if (targetBlocks.length > 0) {
        // Get the last element in the target block to append after
        const lastTargetBlock = targetBlocks[targetBlocks.length - 1];

        // Update the source element's block number
        sourceElement.setAttribute('data-reading-block', newBlockNum);

        // Move the source element after the last target block
        lastTargetBlock.parentNode.insertBefore(sourceElement, lastTargetBlock.nextSibling);

        showNotification(`Merged block ${oldBlockNum} content into block ${newBlockNum}`, 'success');
    } else {
        // No existing target block, just update the block number
        sourceElement.setAttribute('data-reading-block', newBlockNum);
        showNotification(`Changed block number to ${newBlockNum}`, 'success');
    }

    // Recalculate reading orders for all blocks on this page
    recalculateReadingOrders(htmlContainer, page);

    // Re-apply overlays to reflect changes
    setTimeout(() => {
        applyBlockOverlays();
    }, 100);

    APP_STATE.isHtmlEdited = true;
}

// Recalculate reading orders for blocks on a specific page
function recalculateReadingOrders(container, page) {
    // Get all blocks on this page in their current DOM order
    const blocks = container.querySelectorAll(`[data-page="${page}"][data-reading-block]`);

    // Group blocks by reading block number
    const blockGroups = {};
    blocks.forEach(block => {
        const blockNum = block.getAttribute('data-reading-block');
        if (!blockGroups[blockNum]) {
            blockGroups[blockNum] = [];
        }
        blockGroups[blockNum].push(block);
    });

    // Sort block numbers and assign reading orders
    const sortedBlockNums = Object.keys(blockGroups).sort((a, b) => parseInt(a) - parseInt(b));
    let readingOrder = 1;

    sortedBlockNums.forEach(blockNum => {
        blockGroups[blockNum].forEach(block => {
            block.setAttribute('data-reading-order', readingOrder);
            readingOrder++;
        });
    });
}

// Reorder blocks visually based on reading block number
function reorderBlocksVisually(container, page) {
    const blocks = Array.from(container.querySelectorAll(`[data-page="${page}"][data-reading-block]`));

    if (blocks.length === 0) return;

    // Sort blocks by reading block number, then by reading order
    blocks.sort((a, b) => {
        const blockA = parseInt(a.getAttribute('data-reading-block')) || 0;
        const blockB = parseInt(b.getAttribute('data-reading-block')) || 0;
        if (blockA !== blockB) return blockA - blockB;

        const orderA = parseInt(a.getAttribute('data-reading-order')) || 0;
        const orderB = parseInt(b.getAttribute('data-reading-order')) || 0;
        return orderA - orderB;
    });

    // Get the parent container and find a reference point
    const firstBlock = blocks[0];
    const parent = firstBlock.parentNode;

    // Reinsert blocks in sorted order
    blocks.forEach(block => {
        parent.appendChild(block);
    });
}

// Remove block overlays from HTML content
function removeBlockOverlays() {
    const htmlContainer = APP_STATE.currentView === 'html'
        ? document.getElementById('htmlPreview')
        : document.getElementById('htmlEditableContent');

    if (!htmlContainer) return;

    // Remove all overlay elements
    const overlays = htmlContainer.querySelectorAll('.block-info-overlay');
    overlays.forEach(overlay => overlay.remove());

    // Remove border styling
    const blocks = htmlContainer.querySelectorAll('[data-reading-block]');
    blocks.forEach(block => {
        block.style.borderLeft = '';
        if (block.style.position === 'relative' && !block.getAttribute('data-original-position')) {
            block.style.position = '';
        }
    });
}

// Navigate to a specific block
function navigateToBlock(pageNum, blockNum) {
    // First navigate to the page in PDF
    scrollToPage(pageNum);

    // Then find and scroll to the block in HTML
    const htmlContainer = APP_STATE.currentView === 'html'
        ? document.getElementById('htmlPreview')
        : document.getElementById('htmlEditableContent');

    if (!htmlContainer) return;

    // Find element with matching page and block
    let targetElement = htmlContainer.querySelector(
        `[data-page="${pageNum}"][data-reading-block="${blockNum}"]`
    );

    // Fallback to just page if no block match
    if (!targetElement) {
        targetElement = htmlContainer.querySelector(`[data-page="${pageNum}"]`);
    }

    if (targetElement) {
        const containerRect = htmlContainer.getBoundingClientRect();
        const elementRect = targetElement.getBoundingClientRect();
        const relativeTop = elementRect.top - containerRect.top + htmlContainer.scrollTop;

        htmlContainer.scrollTo({
            top: relativeTop - 50,
            behavior: 'smooth'
        });

        // Highlight the element
        targetElement.classList.add('block-highlight');
        setTimeout(() => {
            targetElement.classList.remove('block-highlight');
        }, 2000);
    }
}

// ==========================================
// Table Editor Functions
// ==========================================

// Table editor state
const TABLE_EDITOR_STATE = {
    selectedTable: null,
    selectedCell: null,
    selectedCells: [], // For multi-cell selection
    isSelecting: false,
    selectionStart: null,
    selectionEnd: null
};

// Initialize table editor when HTML editor is active
function initializeTableEditor() {
    const editableContent = document.getElementById('htmlEditableContent');
    if (!editableContent) return;

    // Remove existing listeners to prevent duplicates
    editableContent.removeEventListener('click', handleTableClick);
    editableContent.removeEventListener('mousedown', handleTableMouseDown);
    editableContent.removeEventListener('mousemove', handleTableMouseMove);
    editableContent.removeEventListener('mouseup', handleTableMouseUp);

    // Add table click/selection handlers
    editableContent.addEventListener('click', handleTableClick);
    editableContent.addEventListener('mousedown', handleTableMouseDown);
    editableContent.addEventListener('mousemove', handleTableMouseMove);
    editableContent.addEventListener('mouseup', handleTableMouseUp);

    // Close toolbar when clicking outside
    document.addEventListener('click', handleClickOutsideTable);

    console.log('Table Editor initialized');
}

// Handle click on tables
function handleTableClick(e) {
    const cell = e.target.closest('td, th');
    const table = e.target.closest('table');

    if (cell && table) {
        e.stopPropagation();
        selectCell(cell, table);
        showTableToolbar();
    }
}

// Handle mouse down for cell range selection
function handleTableMouseDown(e) {
    const cell = e.target.closest('td, th');
    const table = e.target.closest('table');

    if (cell && table && e.shiftKey) {
        e.preventDefault();
        TABLE_EDITOR_STATE.isSelecting = true;
        TABLE_EDITOR_STATE.selectionStart = cell;
        TABLE_EDITOR_STATE.selectedTable = table;
        document.getElementById('htmlEditableContent').classList.add('selecting-cells');
    }
}

// Handle mouse move for cell range selection
function handleTableMouseMove(e) {
    if (!TABLE_EDITOR_STATE.isSelecting) return;

    const cell = e.target.closest('td, th');
    if (cell && cell.closest('table') === TABLE_EDITOR_STATE.selectedTable) {
        TABLE_EDITOR_STATE.selectionEnd = cell;
        highlightCellRange();
    }
}

// Handle mouse up for cell range selection
function handleTableMouseUp(e) {
    if (TABLE_EDITOR_STATE.isSelecting) {
        TABLE_EDITOR_STATE.isSelecting = false;
        document.getElementById('htmlEditableContent').classList.remove('selecting-cells');

        // Finalize selection
        if (TABLE_EDITOR_STATE.selectionStart && TABLE_EDITOR_STATE.selectionEnd) {
            selectCellRange(TABLE_EDITOR_STATE.selectionStart, TABLE_EDITOR_STATE.selectionEnd);
        }
    }
}

// Highlight cell range during selection
function highlightCellRange() {
    if (!TABLE_EDITOR_STATE.selectedTable) return;

    // Clear previous highlights
    TABLE_EDITOR_STATE.selectedTable.querySelectorAll('.cell-in-selection').forEach(cell => {
        cell.classList.remove('cell-in-selection');
    });

    if (!TABLE_EDITOR_STATE.selectionStart || !TABLE_EDITOR_STATE.selectionEnd) return;

    const startCoords = getCellCoordinates(TABLE_EDITOR_STATE.selectionStart);
    const endCoords = getCellCoordinates(TABLE_EDITOR_STATE.selectionEnd);

    const minRow = Math.min(startCoords.row, endCoords.row);
    const maxRow = Math.max(startCoords.row, endCoords.row);
    const minCol = Math.min(startCoords.col, endCoords.col);
    const maxCol = Math.max(startCoords.col, endCoords.col);

    const rows = TABLE_EDITOR_STATE.selectedTable.querySelectorAll('tr');
    rows.forEach((row, rowIndex) => {
        if (rowIndex >= minRow && rowIndex <= maxRow) {
            const cells = row.querySelectorAll('td, th');
            cells.forEach((cell, colIndex) => {
                if (colIndex >= minCol && colIndex <= maxCol) {
                    cell.classList.add('cell-in-selection');
                }
            });
        }
    });
}

// Select a range of cells
function selectCellRange(startCell, endCell) {
    if (!TABLE_EDITOR_STATE.selectedTable) return;

    // Clear previous selection
    clearCellSelection();

    const startCoords = getCellCoordinates(startCell);
    const endCoords = getCellCoordinates(endCell);

    const minRow = Math.min(startCoords.row, endCoords.row);
    const maxRow = Math.max(startCoords.row, endCoords.row);
    const minCol = Math.min(startCoords.col, endCoords.col);
    const maxCol = Math.max(startCoords.col, endCoords.col);

    TABLE_EDITOR_STATE.selectedCells = [];

    const rows = TABLE_EDITOR_STATE.selectedTable.querySelectorAll('tr');
    rows.forEach((row, rowIndex) => {
        if (rowIndex >= minRow && rowIndex <= maxRow) {
            const cells = row.querySelectorAll('td, th');
            cells.forEach((cell, colIndex) => {
                if (colIndex >= minCol && colIndex <= maxCol) {
                    cell.classList.add('cell-selected');
                    TABLE_EDITOR_STATE.selectedCells.push(cell);
                }
            });
        }
    });

    updateSelectionInfo();
}

// Get cell coordinates (row, col)
function getCellCoordinates(cell) {
    const row = cell.parentElement;
    const table = row.closest('table');
    const rows = Array.from(table.querySelectorAll('tr'));
    const rowIndex = rows.indexOf(row);
    const cells = Array.from(row.querySelectorAll('td, th'));
    const colIndex = cells.indexOf(cell);

    return { row: rowIndex, col: colIndex };
}

// Select a single cell
function selectCell(cell, table) {
    clearCellSelection();

    TABLE_EDITOR_STATE.selectedTable = table;
    TABLE_EDITOR_STATE.selectedCell = cell;
    TABLE_EDITOR_STATE.selectedCells = [cell];

    cell.classList.add('cell-selected');
    table.classList.add('table-selected');

    updateSelectionInfo();
}

// Clear cell selection
function clearCellSelection() {
    if (TABLE_EDITOR_STATE.selectedTable) {
        TABLE_EDITOR_STATE.selectedTable.querySelectorAll('.cell-selected, .cell-in-selection').forEach(cell => {
            cell.classList.remove('cell-selected', 'cell-in-selection');
        });
        TABLE_EDITOR_STATE.selectedTable.classList.remove('table-selected');
    }
    TABLE_EDITOR_STATE.selectedCells = [];
    TABLE_EDITOR_STATE.selectedCell = null;
}

// Update selection info in toolbar
function updateSelectionInfo() {
    const info = document.getElementById('tableSelectionInfo');
    if (!info) return;

    const count = TABLE_EDITOR_STATE.selectedCells.length;
    if (count === 0) {
        info.textContent = 'Click a cell to select';
    } else if (count === 1) {
        const coords = getCellCoordinates(TABLE_EDITOR_STATE.selectedCells[0]);
        info.textContent = `Selected: Row ${coords.row + 1}, Col ${coords.col + 1}`;
    } else {
        info.textContent = `Selected: ${count} cells`;
    }
}

// Show table toolbar
function showTableToolbar() {
    const toolbar = document.getElementById('tableEditorToolbar');
    if (toolbar) {
        toolbar.style.display = 'block';
    }
}

// Hide table toolbar
function hideTableToolbar() {
    const toolbar = document.getElementById('tableEditorToolbar');
    if (toolbar) {
        toolbar.style.display = 'none';
    }
    clearCellSelection();
    TABLE_EDITOR_STATE.selectedTable = null;
}

// Handle click outside table
function handleClickOutsideTable(e) {
    const toolbar = document.getElementById('tableEditorToolbar');
    const editableContent = document.getElementById('htmlEditableContent');

    // Check if click is inside a table or the toolbar
    const isInTable = e.target.closest('table') && e.target.closest('#htmlEditableContent');
    const isInToolbar = e.target.closest('.table-editor-toolbar');

    if (!isInTable && !isInToolbar && toolbar && toolbar.style.display !== 'none') {
        // Don't hide if we're in html edit mode and clicking on the editable content
        if (!e.target.closest('#htmlEditableContent')) {
            hideTableToolbar();
        }
    }
}

// Get selected row index
function getSelectedRowIndex() {
    if (!TABLE_EDITOR_STATE.selectedCell) return -1;
    const row = TABLE_EDITOR_STATE.selectedCell.parentElement;
    const table = row.closest('table');
    const rows = Array.from(table.querySelectorAll('tr'));
    return rows.indexOf(row);
}

// Get selected column index
function getSelectedColumnIndex() {
    if (!TABLE_EDITOR_STATE.selectedCell) return -1;
    const row = TABLE_EDITOR_STATE.selectedCell.parentElement;
    const cells = Array.from(row.querySelectorAll('td, th'));
    return cells.indexOf(TABLE_EDITOR_STATE.selectedCell);
}

// Add row above selected
function tableAddRowAbove() {
    if (!TABLE_EDITOR_STATE.selectedTable || !TABLE_EDITOR_STATE.selectedCell) {
        showNotification('Please select a cell first', 'warning');
        return;
    }

    const rowIndex = getSelectedRowIndex();
    if (rowIndex < 0) return;

    const table = TABLE_EDITOR_STATE.selectedTable;
    const referenceRow = table.querySelectorAll('tr')[rowIndex];
    const colCount = referenceRow.querySelectorAll('td, th').length;

    const newRow = document.createElement('tr');
    for (let i = 0; i < colCount; i++) {
        const cell = document.createElement('td');
        cell.style.cssText = 'border: 1px solid #666; padding: 0.5rem 0.75rem;';
        cell.innerHTML = '&nbsp;';
        newRow.appendChild(cell);
    }

    referenceRow.parentNode.insertBefore(newRow, referenceRow);
    APP_STATE.isHtmlEdited = true;
    showNotification('Row added above', 'success');
}

// Add row below selected
function tableAddRowBelow() {
    if (!TABLE_EDITOR_STATE.selectedTable || !TABLE_EDITOR_STATE.selectedCell) {
        showNotification('Please select a cell first', 'warning');
        return;
    }

    const rowIndex = getSelectedRowIndex();
    if (rowIndex < 0) return;

    const table = TABLE_EDITOR_STATE.selectedTable;
    const referenceRow = table.querySelectorAll('tr')[rowIndex];
    const colCount = referenceRow.querySelectorAll('td, th').length;

    const newRow = document.createElement('tr');
    for (let i = 0; i < colCount; i++) {
        const cell = document.createElement('td');
        cell.style.cssText = 'border: 1px solid #666; padding: 0.5rem 0.75rem;';
        cell.innerHTML = '&nbsp;';
        newRow.appendChild(cell);
    }

    referenceRow.parentNode.insertBefore(newRow, referenceRow.nextSibling);
    APP_STATE.isHtmlEdited = true;
    showNotification('Row added below', 'success');
}

// Delete selected row
function tableDeleteRow() {
    if (!TABLE_EDITOR_STATE.selectedTable || !TABLE_EDITOR_STATE.selectedCell) {
        showNotification('Please select a cell first', 'warning');
        return;
    }

    const table = TABLE_EDITOR_STATE.selectedTable;
    const rows = table.querySelectorAll('tr');

    if (rows.length <= 1) {
        showNotification('Cannot delete the last row. Delete the table instead.', 'warning');
        return;
    }

    const rowIndex = getSelectedRowIndex();
    if (rowIndex < 0) return;

    const rowToDelete = rows[rowIndex];
    rowToDelete.remove();

    clearCellSelection();
    APP_STATE.isHtmlEdited = true;
    showNotification('Row deleted', 'success');
}

// Add column to the left
function tableAddColumnLeft() {
    if (!TABLE_EDITOR_STATE.selectedTable || !TABLE_EDITOR_STATE.selectedCell) {
        showNotification('Please select a cell first', 'warning');
        return;
    }

    const colIndex = getSelectedColumnIndex();
    if (colIndex < 0) return;

    const table = TABLE_EDITOR_STATE.selectedTable;
    const rows = table.querySelectorAll('tr');

    rows.forEach(row => {
        const cells = row.querySelectorAll('td, th');
        const referenceCell = cells[colIndex];
        const isHeader = referenceCell.tagName === 'TH';

        const newCell = document.createElement(isHeader ? 'th' : 'td');
        newCell.style.cssText = 'border: 1px solid #666; padding: 0.5rem 0.75rem;';
        newCell.innerHTML = '&nbsp;';

        row.insertBefore(newCell, referenceCell);
    });

    APP_STATE.isHtmlEdited = true;
    showNotification('Column added to the left', 'success');
}

// Add column to the right
function tableAddColumnRight() {
    if (!TABLE_EDITOR_STATE.selectedTable || !TABLE_EDITOR_STATE.selectedCell) {
        showNotification('Please select a cell first', 'warning');
        return;
    }

    const colIndex = getSelectedColumnIndex();
    if (colIndex < 0) return;

    const table = TABLE_EDITOR_STATE.selectedTable;
    const rows = table.querySelectorAll('tr');

    rows.forEach(row => {
        const cells = row.querySelectorAll('td, th');
        const referenceCell = cells[colIndex];
        const isHeader = referenceCell.tagName === 'TH';

        const newCell = document.createElement(isHeader ? 'th' : 'td');
        newCell.style.cssText = 'border: 1px solid #666; padding: 0.5rem 0.75rem;';
        newCell.innerHTML = '&nbsp;';

        row.insertBefore(newCell, referenceCell.nextSibling);
    });

    APP_STATE.isHtmlEdited = true;
    showNotification('Column added to the right', 'success');
}

// Delete selected column
function tableDeleteColumn() {
    if (!TABLE_EDITOR_STATE.selectedTable || !TABLE_EDITOR_STATE.selectedCell) {
        showNotification('Please select a cell first', 'warning');
        return;
    }

    const colIndex = getSelectedColumnIndex();
    if (colIndex < 0) return;

    const table = TABLE_EDITOR_STATE.selectedTable;
    const rows = table.querySelectorAll('tr');

    // Check if this is the last column
    const firstRowCells = rows[0].querySelectorAll('td, th');
    if (firstRowCells.length <= 1) {
        showNotification('Cannot delete the last column. Delete the table instead.', 'warning');
        return;
    }

    rows.forEach(row => {
        const cells = row.querySelectorAll('td, th');
        if (cells[colIndex]) {
            cells[colIndex].remove();
        }
    });

    clearCellSelection();
    APP_STATE.isHtmlEdited = true;
    showNotification('Column deleted', 'success');
}

// Merge selected cells
function tableMergeCells() {
    if (!TABLE_EDITOR_STATE.selectedTable || TABLE_EDITOR_STATE.selectedCells.length < 2) {
        showNotification('Please select multiple cells to merge (Shift+click)', 'warning');
        return;
    }

    const cells = TABLE_EDITOR_STATE.selectedCells;

    // Get the bounding box of selected cells
    let minRow = Infinity, maxRow = -1, minCol = Infinity, maxCol = -1;
    cells.forEach(cell => {
        const coords = getCellCoordinates(cell);
        minRow = Math.min(minRow, coords.row);
        maxRow = Math.max(maxRow, coords.row);
        minCol = Math.min(minCol, coords.col);
        maxCol = Math.max(maxCol, coords.col);
    });

    const rowSpan = maxRow - minRow + 1;
    const colSpan = maxCol - minCol + 1;

    // Collect content from all cells
    let mergedContent = '';
    cells.forEach(cell => {
        const content = cell.innerHTML.trim();
        if (content && content !== '&nbsp;') {
            mergedContent += (mergedContent ? ' ' : '') + content;
        }
    });

    // Get the top-left cell as the target
    const rows = TABLE_EDITOR_STATE.selectedTable.querySelectorAll('tr');
    const targetCell = rows[minRow].querySelectorAll('td, th')[minCol];

    // Set colspan and rowspan
    if (colSpan > 1) targetCell.setAttribute('colspan', colSpan);
    if (rowSpan > 1) targetCell.setAttribute('rowspan', rowSpan);
    targetCell.innerHTML = mergedContent || '&nbsp;';

    // Remove other cells (except target)
    cells.forEach(cell => {
        if (cell !== targetCell) {
            cell.remove();
        }
    });

    clearCellSelection();
    selectCell(targetCell, TABLE_EDITOR_STATE.selectedTable);
    APP_STATE.isHtmlEdited = true;
    showNotification('Cells merged', 'success');
}

// Split a merged cell
function tableSplitCell() {
    if (!TABLE_EDITOR_STATE.selectedCell) {
        showNotification('Please select a cell first', 'warning');
        return;
    }

    const cell = TABLE_EDITOR_STATE.selectedCell;
    const colspan = parseInt(cell.getAttribute('colspan')) || 1;
    const rowspan = parseInt(cell.getAttribute('rowspan')) || 1;

    if (colspan === 1 && rowspan === 1) {
        showNotification('This cell is not merged', 'warning');
        return;
    }

    const table = TABLE_EDITOR_STATE.selectedTable;
    const coords = getCellCoordinates(cell);
    const rows = table.querySelectorAll('tr');
    const content = cell.innerHTML;

    // Remove colspan/rowspan attributes
    cell.removeAttribute('colspan');
    cell.removeAttribute('rowspan');
    cell.innerHTML = content;

    // Add cells to complete the grid
    // First, add cells in the same row for colspan
    const currentRow = rows[coords.row];
    for (let c = 1; c < colspan; c++) {
        const newCell = document.createElement(cell.tagName.toLowerCase());
        newCell.style.cssText = 'border: 1px solid #666; padding: 0.5rem 0.75rem;';
        newCell.innerHTML = '&nbsp;';
        cell.parentNode.insertBefore(newCell, cell.nextSibling);
    }

    // Then add rows below for rowspan
    for (let r = 1; r < rowspan; r++) {
        const targetRow = rows[coords.row + r];
        if (targetRow) {
            for (let c = 0; c < colspan; c++) {
                const newCell = document.createElement('td');
                newCell.style.cssText = 'border: 1px solid #666; padding: 0.5rem 0.75rem;';
                newCell.innerHTML = '&nbsp;';

                // Find the correct position in the row
                const existingCells = targetRow.querySelectorAll('td, th');
                if (existingCells[coords.col]) {
                    targetRow.insertBefore(newCell, existingCells[coords.col]);
                } else {
                    targetRow.appendChild(newCell);
                }
            }
        }
    }

    APP_STATE.isHtmlEdited = true;
    showNotification('Cell split', 'success');
}

// Toggle header row
function tableToggleHeaderRow() {
    if (!TABLE_EDITOR_STATE.selectedTable || !TABLE_EDITOR_STATE.selectedCell) {
        showNotification('Please select a cell first', 'warning');
        return;
    }

    const row = TABLE_EDITOR_STATE.selectedCell.parentElement;
    const cells = row.querySelectorAll('td, th');
    const isCurrentlyHeader = cells[0].tagName === 'TH';

    cells.forEach(cell => {
        const newCell = document.createElement(isCurrentlyHeader ? 'td' : 'th');
        newCell.innerHTML = cell.innerHTML;
        newCell.style.cssText = cell.style.cssText;

        // Copy attributes
        Array.from(cell.attributes).forEach(attr => {
            if (attr.name !== 'style') {
                newCell.setAttribute(attr.name, attr.value);
            }
        });

        // Apply header styling if converting to header
        if (!isCurrentlyHeader) {
            newCell.style.background = '#e6eef5';
            newCell.style.fontWeight = '600';
        } else {
            newCell.style.background = '';
            newCell.style.fontWeight = '';
        }

        cell.parentNode.replaceChild(newCell, cell);
    });

    clearCellSelection();
    APP_STATE.isHtmlEdited = true;
    showNotification(isCurrentlyHeader ? 'Row converted to data cells' : 'Row converted to header', 'success');
}

// Set cell background color
function tableCellBgColor(color) {
    if (TABLE_EDITOR_STATE.selectedCells.length === 0) {
        showNotification('Please select cell(s) first', 'warning');
        return;
    }

    TABLE_EDITOR_STATE.selectedCells.forEach(cell => {
        cell.style.backgroundColor = color;
    });

    APP_STATE.isHtmlEdited = true;
}

// Set cell text color
function tableCellTextColor(color) {
    if (TABLE_EDITOR_STATE.selectedCells.length === 0) {
        showNotification('Please select cell(s) first', 'warning');
        return;
    }

    TABLE_EDITOR_STATE.selectedCells.forEach(cell => {
        cell.style.color = color;
    });

    APP_STATE.isHtmlEdited = true;
}

// Set cell border
function tableCellBorder(borderStyle) {
    if (TABLE_EDITOR_STATE.selectedCells.length === 0 || !borderStyle) {
        return;
    }

    TABLE_EDITOR_STATE.selectedCells.forEach(cell => {
        cell.style.border = borderStyle;
    });

    // Reset select
    document.getElementById('cellBorderStyle').value = '';
    APP_STATE.isHtmlEdited = true;
}

// Set cell text alignment
function tableCellAlign(alignment) {
    if (TABLE_EDITOR_STATE.selectedCells.length === 0) {
        showNotification('Please select cell(s) first', 'warning');
        return;
    }

    TABLE_EDITOR_STATE.selectedCells.forEach(cell => {
        cell.style.textAlign = alignment;
    });

    APP_STATE.isHtmlEdited = true;
}

// Set cell vertical alignment
function tableCellVerticalAlign(alignment) {
    if (TABLE_EDITOR_STATE.selectedCells.length === 0) {
        showNotification('Please select cell(s) first', 'warning');
        return;
    }

    TABLE_EDITOR_STATE.selectedCells.forEach(cell => {
        cell.style.verticalAlign = alignment;
    });

    APP_STATE.isHtmlEdited = true;
}

// Set table width
function tableSetWidth() {
    if (!TABLE_EDITOR_STATE.selectedTable) {
        showNotification('Please select a table first', 'warning');
        return;
    }

    const currentWidth = TABLE_EDITOR_STATE.selectedTable.style.width || '100%';
    const newWidth = prompt('Enter table width (e.g., 100%, 500px, auto):', currentWidth);

    if (newWidth !== null && newWidth.trim() !== '') {
        TABLE_EDITOR_STATE.selectedTable.style.width = newWidth;
        APP_STATE.isHtmlEdited = true;
        showNotification(`Table width set to ${newWidth}`, 'success');
    }
}

// Delete entire table
function tableDelete() {
    if (!TABLE_EDITOR_STATE.selectedTable) {
        showNotification('Please select a table first', 'warning');
        return;
    }

    if (confirm('Are you sure you want to delete this entire table?')) {
        TABLE_EDITOR_STATE.selectedTable.remove();
        hideTableToolbar();
        APP_STATE.isHtmlEdited = true;
        showNotification('Table deleted', 'success');
    }
}

// ==========================================
// End of Table Editor Functions
// ==========================================

// Make functions globally available
window.closeScreenshotDialog = closeScreenshotDialog;
window.saveScreenshot = saveScreenshot;
window.showSaveConfirmDialog = showSaveConfirmDialog;
window.closeSaveConfirmDialog = closeSaveConfirmDialog;
window.confirmSaveAndProcess = confirmSaveAndProcess;
window.refreshImagesInPreview = refreshImagesInPreview;
window.insertLink = insertLink;
window.insertTable = insertTable;
window.showMathSymbolPicker = showMathSymbolPicker;
window.closeMathSymbolDialog = closeMathSymbolDialog;
window.insertSymbol = insertSymbol;
window.toggleBlockOverlay = toggleBlockOverlay;
window.navigateToBlock = navigateToBlock;
window.startInlineEdit = startInlineEdit;
window.handleBlockNumberChange = handleBlockNumberChange;
window.recalculateReadingOrders = recalculateReadingOrders;
window.reorderBlocksVisually = reorderBlocksVisually;
window.toggleScreenshotMode = toggleScreenshotMode;

// Table Editor functions
window.hideTableToolbar = hideTableToolbar;
window.tableAddRowAbove = tableAddRowAbove;
window.tableAddRowBelow = tableAddRowBelow;
window.tableDeleteRow = tableDeleteRow;
window.tableAddColumnLeft = tableAddColumnLeft;
window.tableAddColumnRight = tableAddColumnRight;
window.tableDeleteColumn = tableDeleteColumn;
window.tableMergeCells = tableMergeCells;
window.tableSplitCell = tableSplitCell;
window.tableToggleHeaderRow = tableToggleHeaderRow;
window.tableCellBgColor = tableCellBgColor;
window.tableCellTextColor = tableCellTextColor;
window.tableCellBorder = tableCellBorder;
window.tableCellAlign = tableCellAlign;
window.tableCellVerticalAlign = tableCellVerticalAlign;
window.tableSetWidth = tableSetWidth;
window.tableDelete = tableDelete;
