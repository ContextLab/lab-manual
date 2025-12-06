// Generate table of contents from h2.chapterHead elements
document.addEventListener('DOMContentLoaded', function() {
  var toc = document.getElementById('toc-list');
  if (!toc) return;

  // Clear existing TOC items (tex4ht generates broken links)
  toc.innerHTML = '';

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
