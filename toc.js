// Generate table of contents from h2.chapterHead elements
document.addEventListener('DOMContentLoaded', function() {
  var toc = document.getElementById('toc-list');
  if (!toc) return;

  while (toc.firstChild) toc.removeChild(toc.firstChild);

  var headings = document.querySelectorAll('h2.chapterHead');
  headings.forEach(function(h, index) {
    if (!h.id || h.id === '') {
      var slug = h.textContent
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-|-$/g, '');
      h.id = slug || 'section-' + index;
    }

    var li = document.createElement('li');
    var a = document.createElement('a');
    a.href = '#' + h.id;
    a.textContent = h.textContent;
    li.appendChild(a);
    toc.appendChild(li);
  });

  // Wrap personnel names in .columns-2 sections into individual spans.
  // tex4ht outputs names as raw text in a <p>; CSS grid needs elements.
  // Each name matches "Name (year – )" or "Name (year – year)" or "Name (year)".
  var cols = document.querySelectorAll('.columns-2');
  cols.forEach(function(col) {
    var ps = col.querySelectorAll('p');
    ps.forEach(function(p) {
      var text = p.textContent.trim();
      if (!text) return;

      // Split on the pattern: end of one entry ")" followed by start of next
      var names = text.match(/[^)]+\)/g);
      if (!names || names.length === 0) return;

      // Replace the <p> contents with wrapped spans
      while (p.firstChild) p.removeChild(p.firstChild);
      names.forEach(function(name) {
        var span = document.createElement('span');
        span.className = 'personnel-item';
        span.textContent = name.trim();
        p.appendChild(span);
      });
    });

    // Move children out of <p> into the columns-2 div directly for grid layout
    var items = col.querySelectorAll('.personnel-item');
    items.forEach(function(item) {
      col.appendChild(item);
    });
    // Remove now-empty <p> elements
    var emptyPs = col.querySelectorAll('p');
    emptyPs.forEach(function(p) {
      if (p.textContent.trim() === '') p.remove();
    });
  });
});

// Prevent overlapping margin notes inside definition lists.
// CSS positions dd margin notes with position:absolute (removing them
// from flow so dd doesn't expand). But adjacent notes can overlap.
// This script nudges overlapping notes down with a small gap.
window.addEventListener('load', function() {
  var dls = document.querySelectorAll('article > dl');
  dls.forEach(function(dl) {
    var notes = dl.querySelectorAll('dd .marginnote, dd .sidenote');
    if (notes.length < 2) return;

    for (var i = 1; i < notes.length; i++) {
      var prev = notes[i - 1].getBoundingClientRect();
      var curr = notes[i].getBoundingClientRect();
      var overlap = prev.bottom - curr.top;
      if (overlap > 0) {
        var currentTop = parseFloat(getComputedStyle(notes[i]).top) || 0;
        notes[i].style.top = (currentTop + overlap + 8) + 'px';
      }
    }
  });
});
