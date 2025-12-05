// Generate table of contents from h2.chapterHead elements
document.addEventListener('DOMContentLoaded', function() {
  var toc = document.getElementById('toc-list');
  if (!toc) return;

  var headings = document.querySelectorAll('h2.chapterHead');
  headings.forEach(function(h) {
    var li = document.createElement('li');
    var a = document.createElement('a');
    a.href = '#' + h.id;
    a.textContent = h.textContent;
    li.appendChild(a);
    toc.appendChild(li);
  });
});
