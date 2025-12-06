// Interactive checklist functionality for lab manual HTML version
document.addEventListener('DOMContentLoaded', function() {
  // Fix enumeration numbering issues (tex4ht generates "1.2.3.1." instead of "1.")
  fixEnumerationNumbering();

  // Wrap orphaned text nodes after h2 headings in containers for proper styling
  wrapOrphanedTextNodes();

  // Format personnel lists into two-column grid layout
  formatPersonnelLists();

  // Convert the checklist section to interactive form
  // Need to wait a bit for MathJax to potentially process, but also handle native MathML
  setTimeout(convertChecklistToInteractive, 100);
});

// Wrap orphaned text nodes after h2 headings in containers
// tex4ht sometimes generates text directly after h2 without wrapping in <p>
// IMPORTANT: Skip the checklist section - it needs special handling
function wrapOrphanedTextNodes() {
  var h2s = document.querySelectorAll('h2');
  h2s.forEach(function(h2) {
    // Skip the checklist section - it will be handled by convertChecklistToInteractive
    if (h2.textContent.toLowerCase().includes('checklist') &&
        h2.textContent.toLowerCase().includes('signature')) {
      return;
    }

    var sibling = h2.nextSibling;
    var nodesToWrap = [];

    // Collect consecutive non-section, non-h2 nodes after the h2
    while (sibling) {
      if (sibling.nodeType === Node.ELEMENT_NODE) {
        var tagName = sibling.tagName.toUpperCase();
        // Stop at next major section
        if (tagName === 'H2' || tagName === 'SECTION' || tagName === 'NAV') {
          break;
        }
        // Stop if we hit a paragraph or list - those are already structured
        if (tagName === 'P' || tagName === 'UL' || tagName === 'OL' || tagName === 'DL') {
          break;
        }
      }

      // Only collect text nodes and inline elements that appear before the first block element
      if (sibling.nodeType === Node.TEXT_NODE && sibling.textContent.trim()) {
        nodesToWrap.push(sibling);
      } else if (sibling.nodeType === Node.ELEMENT_NODE) {
        var tagName = sibling.tagName.toUpperCase();
        // Inline elements that are part of the intro text
        if (tagName === 'A' || tagName === 'SPAN' || tagName === 'LABEL' ||
            tagName === 'INPUT' || tagName === 'MATH' || tagName === 'MJX-CONTAINER') {
          nodesToWrap.push(sibling);
        } else {
          break;
        }
      }
      sibling = sibling.nextSibling;
    }

    // If we found orphaned nodes, wrap them in a paragraph
    if (nodesToWrap.length > 0) {
      var wrapper = document.createElement('p');
      wrapper.className = 'section-intro';
      h2.parentNode.insertBefore(wrapper, nodesToWrap[0]);
      nodesToWrap.forEach(function(node) {
        wrapper.appendChild(node);
      });
    }
  });
}

// Fix the enumeration numbering issues caused by tex4ht
function fixEnumerationNumbering() {
  // Find all dt elements that have malformed numbers like "1.2.3.1."
  var dts = document.querySelectorAll('dt');
  dts.forEach(function(dt) {
    var text = dt.textContent.trim();
    // Match patterns like "1.2.3.1." or "1.2.3.1" (multiple dots with numbers)
    if (/^\d+(\.\d+){2,}\.*$/.test(text)) {
      // Extract just the last number
      var parts = text.replace(/\.+$/, '').split('.');
      var lastNum = parts[parts.length - 1];
      dt.textContent = lastNum + '.';
    }
  });
}

// Format personnel lists (lab members) into two-column grid layout
// tex4ht generates text nodes directly in divs with class "columns-2"
// CSS grid needs child elements, so we wrap each name in a span
function formatPersonnelLists() {
  // Find all columns-2 divs (tex4ht generates these for the \begin{columns} environment)
  var columnsDivs = document.querySelectorAll('div.columns-2');

  columnsDivs.forEach(function(div) {
    // Get the text content and split into individual names
    var text = div.textContent || '';

    // Split by newlines and filter out empty entries
    var names = text.split(/[\n\r]+/)
      .map(function(s) { return s.trim(); })
      .filter(function(s) { return s.length > 0; });

    // Only process if we have names
    if (names.length === 0) return;

    // Clear the div and add wrapped names
    div.innerHTML = '';

    names.forEach(function(name) {
      var span = document.createElement('span');
      span.className = 'personnel-item';
      span.textContent = name;
      div.appendChild(span);
    });
  });
}

// Convert the static checklist to an interactive form
function convertChecklistToInteractive() {
  // Find the checklist section by looking for "Checklist and signature page" heading
  var checklistHeading = null;
  var headings = document.querySelectorAll('h2');
  headings.forEach(function(h) {
    if (h.textContent.toLowerCase().includes('checklist') &&
        h.textContent.toLowerCase().includes('signature')) {
      checklistHeading = h;
    }
  });

  if (!checklistHeading) return;

  // tex4ht generates a complex structure where checklist items are spread across
  // sibling nodes after the h2 heading:
  // - MJX-CONTAINER elements contain the checkbox character (□)
  // - TEXT nodes contain the actual item text
  // - SPAN and A elements contain inline formatting
  // We use nextSibling to iterate through ALL siblings (including text nodes)

  // Collect all sibling nodes after the heading until we hit a TABLE or end
  var nodesToHide = [];
  var signatureTable = null;
  var sibling = checklistHeading.nextSibling;

  while (sibling) {
    // Check if this is the signature table
    if (sibling.nodeType === Node.ELEMENT_NODE) {
      if (sibling.tagName === 'TABLE') {
        signatureTable = sibling;
        break;
      }
      // Also check for next section (another h2)
      if (sibling.tagName === 'H2') {
        break;
      }
    }
    nodesToHide.push(sibling);
    sibling = sibling.nextSibling;
  }

  // Now extract checklist items by walking through nodes and building items
  // Each item starts after a MJX-CONTAINER (checkbox) and continues until the next one
  var items = [];
  var currentItem = '';
  var foundFirstCheckbox = false;

  nodesToHide.forEach(function(node) {
    // Check if this node contains a checkbox character
    // Handle both native MathML (<math>) and MathJax-rendered (MJX-CONTAINER) elements
    var isCheckbox = false;
    if (node.nodeType === Node.ELEMENT_NODE) {
      var tagName = node.tagName.toUpperCase();
      if (tagName === 'MJX-CONTAINER' || tagName === 'MATH') {
        isCheckbox = true;
      }
    }

    if (isCheckbox) {
      // Save the previous item if we have one
      if (foundFirstCheckbox && currentItem.trim().length > 15) {
        items.push(currentItem.trim());
      }
      currentItem = '';
      foundFirstCheckbox = true;
    } else if (foundFirstCheckbox) {
      // Add text content to current item
      if (node.nodeType === Node.TEXT_NODE) {
        currentItem += node.textContent || '';
      } else if (node.nodeType === Node.ELEMENT_NODE) {
        // For elements like SPAN and A, get their text content
        // But skip the first DIV which has duplicate content
        if (node.tagName !== 'DIV') {
          currentItem += node.textContent || '';
        }
      }
    }
  });

  // Don't forget the last item
  if (currentItem.trim().length > 15) {
    items.push(currentItem.trim());
  }

  // Clean up items - remove extra whitespace
  items = items.map(function(item) {
    return item.replace(/\s+/g, ' ').trim();
  });

  if (items.length === 0) return;

  // Create the interactive checklist container
  var container = document.createElement('div');
  container.className = 'interactive-checklist';
  container.id = 'interactive-checklist';

  // Add intro text
  var intro = document.createElement('p');
  intro.textContent = 'By signing below, I certify that I have completed the following tasks:';
  container.appendChild(intro);

  // Create checklist items
  items.forEach(function(itemText, index) {
    var itemDiv = document.createElement('div');
    itemDiv.className = 'checklist-item';

    var checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.id = 'checklist-item-' + index;
    checkbox.name = 'checklist-item-' + index;

    var label = document.createElement('label');
    label.htmlFor = 'checklist-item-' + index;
    label.innerHTML = itemText;

    // Toggle completed class on check
    checkbox.addEventListener('change', function() {
      if (this.checked) {
        itemDiv.classList.add('completed');
      } else {
        itemDiv.classList.remove('completed');
      }
      saveChecklistState();
    });

    itemDiv.appendChild(checkbox);
    itemDiv.appendChild(label);
    container.appendChild(itemDiv);
  });

  // Create signature section
  var signatureSection = document.createElement('div');
  signatureSection.className = 'signature-section';
  signatureSection.innerHTML = '<h3>Signature</h3>';

  // Signature canvas
  var canvasContainer = document.createElement('div');
  canvasContainer.className = 'signature-canvas-container';

  var signatureLabel = document.createElement('label');
  signatureLabel.textContent = 'Sign below (draw your signature):';
  canvasContainer.appendChild(signatureLabel);

  var canvas = document.createElement('canvas');
  canvas.id = 'signature-canvas';
  canvas.className = 'signature-canvas';
  canvas.width = 400;
  canvas.height = 150;
  canvasContainer.appendChild(canvas);

  var clearBtn = document.createElement('button');
  clearBtn.type = 'button';
  clearBtn.className = 'clear-signature-btn';
  clearBtn.textContent = 'Clear Signature';
  clearBtn.onclick = function() { clearSignature(); };
  canvasContainer.appendChild(document.createElement('br'));
  canvasContainer.appendChild(clearBtn);

  signatureSection.appendChild(canvasContainer);

  // Date field
  var dateField = document.createElement('div');
  dateField.className = 'signature-field';
  dateField.innerHTML = '<label for="signature-date">Date:</label>';
  var dateInput = document.createElement('input');
  dateInput.type = 'date';
  dateInput.id = 'signature-date';
  dateInput.name = 'signature-date';
  dateInput.value = new Date().toISOString().split('T')[0]; // Today's date
  dateInput.addEventListener('change', saveChecklistState);
  dateField.appendChild(dateInput);
  signatureSection.appendChild(dateField);

  // Email button
  var emailBtn = document.createElement('button');
  emailBtn.type = 'button';
  emailBtn.className = 'email-checklist-btn';
  emailBtn.textContent = 'Email Completed Checklist to contextualdynamics@gmail.com';
  emailBtn.onclick = function() { emailChecklist(); };
  signatureSection.appendChild(emailBtn);

  container.appendChild(signatureSection);

  // Hide all the original checklist nodes (elements and text nodes)
  nodesToHide.forEach(function(node) {
    if (node.nodeType === Node.ELEMENT_NODE) {
      node.style.display = 'none';
    } else if (node.nodeType === Node.TEXT_NODE) {
      // For text nodes, we need to remove them or replace with empty
      // We'll wrap them in a span and hide it
      if (node.textContent.trim()) {
        var wrapper = document.createElement('span');
        wrapper.style.display = 'none';
        node.parentNode.insertBefore(wrapper, node);
        wrapper.appendChild(node);
      }
    }
  });

  // Hide signature table if found
  if (signatureTable) {
    signatureTable.style.display = 'none';
  }

  // Insert the interactive checklist after the heading
  checklistHeading.parentNode.insertBefore(container, checklistHeading.nextSibling);

  // Initialize signature canvas
  initSignatureCanvas();

  // Load saved state
  loadChecklistState();
}

// Signature canvas functionality
var signatureCanvas, signatureCtx, isDrawing = false;

function initSignatureCanvas() {
  signatureCanvas = document.getElementById('signature-canvas');
  if (!signatureCanvas) return;

  signatureCtx = signatureCanvas.getContext('2d');
  signatureCtx.strokeStyle = '#000';
  signatureCtx.lineWidth = 2;
  signatureCtx.lineCap = 'round';
  signatureCtx.lineJoin = 'round';

  // Mouse events
  signatureCanvas.addEventListener('mousedown', startDrawing);
  signatureCanvas.addEventListener('mousemove', draw);
  signatureCanvas.addEventListener('mouseup', stopDrawing);
  signatureCanvas.addEventListener('mouseout', stopDrawing);

  // Touch events
  signatureCanvas.addEventListener('touchstart', function(e) {
    e.preventDefault();
    var touch = e.touches[0];
    var mouseEvent = new MouseEvent('mousedown', {
      clientX: touch.clientX,
      clientY: touch.clientY
    });
    signatureCanvas.dispatchEvent(mouseEvent);
  });

  signatureCanvas.addEventListener('touchmove', function(e) {
    e.preventDefault();
    var touch = e.touches[0];
    var mouseEvent = new MouseEvent('mousemove', {
      clientX: touch.clientX,
      clientY: touch.clientY
    });
    signatureCanvas.dispatchEvent(mouseEvent);
  });

  signatureCanvas.addEventListener('touchend', function(e) {
    e.preventDefault();
    var mouseEvent = new MouseEvent('mouseup', {});
    signatureCanvas.dispatchEvent(mouseEvent);
  });
}

function getMousePos(canvas, e) {
  var rect = canvas.getBoundingClientRect();
  return {
    x: e.clientX - rect.left,
    y: e.clientY - rect.top
  };
}

function startDrawing(e) {
  isDrawing = true;
  var pos = getMousePos(signatureCanvas, e);
  signatureCtx.beginPath();
  signatureCtx.moveTo(pos.x, pos.y);
}

function draw(e) {
  if (!isDrawing) return;
  var pos = getMousePos(signatureCanvas, e);
  signatureCtx.lineTo(pos.x, pos.y);
  signatureCtx.stroke();
}

function stopDrawing() {
  if (isDrawing) {
    isDrawing = false;
    saveChecklistState();
  }
}

function clearSignature() {
  if (signatureCtx) {
    signatureCtx.clearRect(0, 0, signatureCanvas.width, signatureCanvas.height);
    saveChecklistState();
  }
}

// Save/load state to localStorage
function saveChecklistState() {
  var state = {
    checkboxes: [],
    date: document.getElementById('signature-date')?.value || '',
    signature: signatureCanvas ? signatureCanvas.toDataURL() : ''
  };

  var checkboxes = document.querySelectorAll('.interactive-checklist input[type="checkbox"]');
  checkboxes.forEach(function(cb) {
    state.checkboxes.push(cb.checked);
  });

  try {
    localStorage.setItem('labManualChecklist', JSON.stringify(state));
  } catch (e) {
    // localStorage not available
  }
}

function loadChecklistState() {
  try {
    var saved = localStorage.getItem('labManualChecklist');
    if (!saved) return;

    var state = JSON.parse(saved);

    // Restore checkboxes
    var checkboxes = document.querySelectorAll('.interactive-checklist input[type="checkbox"]');
    checkboxes.forEach(function(cb, index) {
      if (state.checkboxes && state.checkboxes[index]) {
        cb.checked = true;
        cb.parentElement.classList.add('completed');
      }
    });

    // Restore date
    if (state.date) {
      var dateInput = document.getElementById('signature-date');
      if (dateInput) dateInput.value = state.date;
    }

    // Restore signature
    if (state.signature && signatureCanvas) {
      var img = new Image();
      img.onload = function() {
        signatureCtx.drawImage(img, 0, 0);
      };
      img.src = state.signature;
    }
  } catch (e) {
    // Error loading state
  }
}

// Email functionality - generates PDF with jsPDF and opens email client
function emailChecklist() {
  // Check if all items are checked
  var checkboxes = document.querySelectorAll('.interactive-checklist input[type="checkbox"]');
  var allChecked = true;
  checkboxes.forEach(function(cb) {
    if (!cb.checked) allChecked = false;
  });

  if (!allChecked) {
    alert('Please check all items before submitting.');
    return;
  }

  // Check if signature exists
  if (signatureCanvas) {
    var ctx = signatureCanvas.getContext('2d');
    var pixelData = ctx.getImageData(0, 0, signatureCanvas.width, signatureCanvas.height).data;
    var hasSignature = false;
    for (var i = 3; i < pixelData.length; i += 4) {
      if (pixelData[i] > 0) {
        hasSignature = true;
        break;
      }
    }
    if (!hasSignature) {
      alert('Please add your signature before submitting.');
      return;
    }
  }

  // Check date
  var dateInput = document.getElementById('signature-date');
  if (!dateInput || !dateInput.value) {
    alert('Please enter the date before submitting.');
    return;
  }

  // Generate and download PDF, then open email
  generateChecklistPDF(dateInput.value);
}

// Generate PDF using jsPDF library
function generateChecklistPDF(dateValue) {
  // Check if jsPDF is available
  if (typeof window.jspdf === 'undefined' && typeof jsPDF === 'undefined') {
    // Load jsPDF dynamically
    var script = document.createElement('script');
    script.src = 'https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js';
    script.onload = function() {
      createPDF(dateValue);
    };
    script.onerror = function() {
      // Fallback to print dialog
      alert('Could not load PDF library. Using print dialog instead.');
      fallbackToPrint(dateValue);
    };
    document.head.appendChild(script);
  } else {
    createPDF(dateValue);
  }
}

function createPDF(dateValue) {
  var jsPDFLib = window.jspdf || { jsPDF: jsPDF };
  var doc = new jsPDFLib.jsPDF({
    unit: 'mm',
    format: 'letter'  // US Letter size like the original PDF
  });

  // Tufte-style layout constants
  // Page is 215.9mm x 279.4mm (letter)
  // Content area is ~55% of page width, leaving wide right margin
  var pageWidth = 215.9;
  var pageHeight = 279.4;
  var leftMargin = 25;  // Left margin
  var contentWidth = 100;  // ~55% of usable width for main content
  var topMargin = 25;
  var bottomMargin = 25;

  // Use Times/Palatino-like font (closest to ET Book in jsPDF)
  var mainFont = 'times';

  // Page 1: Checklist title and intro
  var yPos = topMargin;

  // Section heading - Tufte style (italic, larger)
  doc.setFont(mainFont, 'italic');
  doc.setFontSize(14);
  doc.text('Checklist and signature page', leftMargin, yPos);
  yPos += 12;

  // Intro paragraph
  doc.setFont(mainFont, 'normal');
  doc.setFontSize(11);
  var introText = 'By signing below, I certify that I have completed the following tasks:';
  doc.text(introText, leftMargin, yPos);
  yPos += 10;

  // Checklist items with checkbox symbols
  var checkboxes = document.querySelectorAll('.interactive-checklist input[type="checkbox"]');
  var labels = document.querySelectorAll('.interactive-checklist label');

  doc.setFontSize(10);
  labels.forEach(function(label, index) {
    var isChecked = checkboxes[index] && checkboxes[index].checked;
    var checkSymbol = isChecked ? '\u2611' : '\u2610';  // ☑ or ☐
    var text = label.textContent.trim();

    // Wrap text to content width
    var wrappedLines = doc.splitTextToSize(text, contentWidth - 8);

    // Check if we need a new page
    var itemHeight = wrappedLines.length * 4.5 + 3;
    if (yPos + itemHeight > pageHeight - bottomMargin) {
      doc.addPage();
      yPos = topMargin;
    }

    // Draw checkbox symbol
    doc.setFont(mainFont, 'normal');
    doc.text(checkSymbol, leftMargin, yPos);

    // Draw wrapped text with proper indentation
    wrappedLines.forEach(function(line, lineIndex) {
      doc.text(line, leftMargin + 6, yPos + (lineIndex * 4.5));
    });

    yPos += itemHeight;
  });

  // Signature section
  // Check if we need a new page for signature
  if (yPos + 60 > pageHeight - bottomMargin) {
    doc.addPage();
    yPos = topMargin;
  }

  yPos += 8;

  // Signature line with label (Tufte style - simple, elegant)
  doc.setFont(mainFont, 'normal');
  doc.setFontSize(10);

  // Add signature image if exists
  if (signatureCanvas) {
    var signatureData = signatureCanvas.toDataURL('image/png');
    // Position signature above the line
    doc.addImage(signatureData, 'PNG', leftMargin, yPos, 60, 22);
    yPos += 24;
  }

  // Signature line
  doc.setDrawColor(0);
  doc.setLineWidth(0.3);
  doc.line(leftMargin, yPos, leftMargin + 70, yPos);
  yPos += 4;
  doc.setFontSize(9);
  doc.text('Signature', leftMargin, yPos);

  // Date section
  yPos += 12;
  doc.setFontSize(10);
  doc.text(dateValue, leftMargin, yPos);
  yPos += 2;
  doc.line(leftMargin, yPos, leftMargin + 40, yPos);
  yPos += 4;
  doc.setFontSize(9);
  doc.text('Date', leftMargin, yPos);

  // Add page numbers in footer (Tufte style - centered at bottom)
  var pageCount = doc.internal.getNumberOfPages();
  for (var i = 1; i <= pageCount; i++) {
    doc.setPage(i);
    doc.setFont(mainFont, 'normal');
    doc.setFontSize(9);
    doc.text(String(i), pageWidth / 2, pageHeight - 15, { align: 'center' });
  }

  // Save the PDF
  var filename = 'lab-manual-checklist-' + dateValue + '.pdf';
  doc.save(filename);

  // Open email client after a short delay
  setTimeout(function() {
    var subject = encodeURIComponent('Lab Manual Checklist - Completed');
    var body = encodeURIComponent(
      'I have completed the Lab Manual checklist.\n\n' +
      'Date: ' + dateValue + '\n\n' +
      'Please find the signed checklist PDF attached to this email.\n' +
      '(The PDF was just downloaded to your computer - please attach it to this email.)\n\n' +
      'Thank you!'
    );
    window.location.href = 'mailto:contextualdynamics@gmail.com?subject=' + subject + '&body=' + body;
  }, 500);
}

function fallbackToPrint(dateValue) {
  var subject = encodeURIComponent('Lab Manual Checklist - Completed');
  var body = encodeURIComponent(
    'I have completed the Lab Manual checklist and attached a PDF of the signed checklist page.\n\n' +
    'Date: ' + dateValue + '\n\n' +
    'To generate a PDF:\n' +
    '1. Print this page (Ctrl/Cmd + P)\n' +
    '2. Select "Save as PDF" as the destination\n' +
    '3. Save and attach to this email\n\n' +
    'Alternatively, you can take a screenshot of the completed checklist.'
  );

  window.location.href = 'mailto:contextualdynamics@gmail.com?subject=' + subject + '&body=' + body;

  setTimeout(function() {
    if (confirm('Would you like to print/save this page as PDF to attach to the email?')) {
      window.print();
    }
  }, 500);
}
