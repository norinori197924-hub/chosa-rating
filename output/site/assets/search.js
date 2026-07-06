(function () {
  "use strict";
  var dataEl = document.getElementById("site-data");
  if (!dataEl) return;

  var entries;
  try {
    entries = JSON.parse(dataEl.textContent);
  } catch (err) {
    return;
  }

  var searchInput = document.getElementById("site-search");
  var genreSelect = document.getElementById("filter-genre");
  var targetSelect = document.getElementById("filter-target");
  var gradeSelect = document.getElementById("filter-grade");
  var periodSelect = document.getElementById("filter-period");
  var resultCount = document.getElementById("result-count");
  var noResults = document.getElementById("no-results");
  if (!searchInput || !genreSelect || !targetSelect || !gradeSelect || !periodSelect) return;

  var cardById = {};
  document.querySelectorAll(".release-card[data-id]").forEach(function (card) {
    cardById[card.getAttribute("data-id")] = card;
  });

  function normalize(value) {
    return (value || "").toString().toLowerCase();
  }

  function matchesEntry(entry, query) {
    if (query) {
      var haystack = normalize(entry.title) + " " + normalize(entry.body_text);
      if (haystack.indexOf(query) === -1) return false;
    }
    if (genreSelect.value && entry.genre !== genreSelect.value) return false;
    if (targetSelect.value && entry.target !== targetSelect.value) return false;
    if (gradeSelect.value && entry.grade !== gradeSelect.value) return false;
    if (periodSelect.value) {
      var days = parseInt(periodSelect.value, 10);
      var published = new Date(entry.date);
      if (isNaN(published.getTime())) return false;
      var cutoffMs = Date.now() - days * 24 * 60 * 60 * 1000;
      if (published.getTime() < cutoffMs) return false;
    }
    return true;
  }

  function applyFilters() {
    var query = normalize(searchInput.value.trim());
    var visible = 0;
    entries.forEach(function (entry) {
      var card = cardById[entry.id];
      if (!card) return;
      var ok = matchesEntry(entry, query);
      card.style.display = ok ? "" : "none";
      if (ok) visible += 1;
    });
    if (resultCount) {
      resultCount.textContent = visible + " 件表示中(全 " + entries.length + " 件)";
    }
    if (noResults) {
      noResults.classList.toggle("is-visible", visible === 0);
    }
  }

  [searchInput, genreSelect, targetSelect, gradeSelect, periodSelect].forEach(function (el) {
    el.addEventListener("input", applyFilters);
    el.addEventListener("change", applyFilters);
  });

  applyFilters();
})();
