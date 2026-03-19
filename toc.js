// Generate table of contents from h2.chapterHead elements
document.addEventListener('DOMContentLoaded', function() {
  var toc = document.getElementById('toc-list');
  if (!toc) return;

  // Clear existing TOC items (tex4ht generates broken links)
  while (toc.firstChild) toc.removeChild(toc.firstChild);

  var headings = document.querySelectorAll('h2.chapterHead');
  headings.forEach(function(h, index) {
    // Generate an ID if heading doesn't have one or it's empty
    if (!h.id || h.id === '') {
      // Create slug from heading text
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
});

// Fix margin notes inside definition lists.
// Floated margin notes inside dd elements expand the dd height,
// causing large gaps in the left-side text. We reposition them
// as absolutely positioned elements relative to the article.
// Runs on window load (after layout) so getBoundingClientRect is accurate.
window.addEventListener('load', function() {
  var article = document.querySelector('article');
  if (!article) return;

  article.style.position = 'relative';

  var ddNotes = document.querySelectorAll('dd .marginnote, dd .sidenote');
  var articleRect = article.getBoundingClientRect();

  ddNotes.forEach(function(note) {
    var parent = note.parentElement;
    var anchorRect = parent.getBoundingClientRect();
    var topOffset = anchorRect.top - articleRect.top;

    note.style.position = 'absolute';
    note.style.top = topOffset + 'px';
    note.style.right = '0';
    note.style.width = '25%';
    note.style.marginRight = '0';
    note.style.float = 'none';
    note.style.clear = 'none';

    article.appendChild(note);
  });
});
