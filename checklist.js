// Interactive checklist functionality for lab manual HTML version
document.addEventListener('DOMContentLoaded', function() {
  // Fix enumeration numbering issues (tex4ht generates "1.2.3.1." instead of "1.")
  fixEnumerationNumbering();

  // Convert the checklist section to interactive form
  convertChecklistToInteractive();
});

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
  // many nodes: MJX-CONTAINER (for checkbox chars), TEXT nodes, SPAN, A elements
  // The actual item text is in TEXT NODES between elements, not inside elements
  // We need to iterate over ALL child nodes, not just element siblings

  var parent = checklistHeading.parentElement;
  if (!parent) return;

  // Find the index of the heading in parent's childNodes
  var startIndex = -1;
  var childNodes = parent.childNodes;
  for (var i = 0; i < childNodes.length; i++) {
    if (childNodes[i] === checklistHeading) {
      startIndex = i;
      break;
    }
  }
  if (startIndex === -1) return;

  // Collect all nodes (elements AND text nodes) after the heading until signature table
  var nodesToHide = [];
  var signatureTable = null;
  var fullText = '';

  for (var i = startIndex + 1; i < childNodes.length; i++) {
    var node = childNodes[i];

    // Check if this is the signature table (TABLE element or contains Signature/Date labels)
    if (node.nodeType === Node.ELEMENT_NODE) {
      if (node.tagName === 'TABLE' ||
          (node.textContent &&
           node.textContent.includes('Signature') &&
           node.textContent.includes('Date') &&
           !node.textContent.includes('tasks'))) {
        signatureTable = node;
        break;
      }
    }

    nodesToHide.push(node);

    // Extract text from both text nodes and elements
    if (node.nodeType === Node.TEXT_NODE) {
      fullText += node.textContent || '';
    } else if (node.nodeType === Node.ELEMENT_NODE) {
      fullText += node.textContent || '';
    }
  }

  // Check if we have checkbox characters
  if (!fullText.includes('\u25A1') && !fullText.includes('\u2610')) {
    return;
  }

  // Split by checkbox character (□ or ☐) and extract items
  var items = [];
  var parts = fullText.split(/[\u25A1\u2610]/);

  parts.forEach(function(part, index) {
    if (index === 0) return; // Skip text before first checkbox
    var cleanText = part.trim();
    // Remove leading/trailing punctuation artifacts
    cleanText = cleanText.replace(/^[\s,]+/, '').replace(/[\s,]+$/, '');
    if (cleanText.length > 15) { // Only include substantial items
      items.push(cleanText);
    }
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

// Email functionality
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

  // Generate email with PDF attachment instruction
  var subject = encodeURIComponent('Lab Manual Checklist - Completed');
  var body = encodeURIComponent(
    'I have completed the Lab Manual checklist and attached a PDF of the signed checklist page.\n\n' +
    'Date: ' + dateInput.value + '\n\n' +
    'To generate a PDF:\n' +
    '1. Print this page (Ctrl/Cmd + P)\n' +
    '2. Select "Save as PDF" as the destination\n' +
    '3. Save and attach to this email\n\n' +
    'Alternatively, you can take a screenshot of the completed checklist.'
  );

  // Open email client
  window.location.href = 'mailto:contextualdynamics@gmail.com?subject=' + subject + '&body=' + body;

  // Also show print dialog for PDF generation
  setTimeout(function() {
    if (confirm('Would you like to print/save this page as PDF to attach to the email?')) {
      window.print();
    }
  }, 500);
}
