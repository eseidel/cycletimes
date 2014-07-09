var repositories = repositories || {};

(function(){

var registry = [
  {
    'name': 'chromium',
    'short_name': 'cr',
    'change_url': 'http://crrev.com/%s',
    'changelog_url': 'http://build.chromium.org/f/chromium/perf/dashboard/ui/changelog.html?url=/trunk&range=%s:%s',
  },
  {
    'name': 'blink',
    'short_name': 'bl',
    'change_url': 'https://src.chromium.org/viewvc/blink?view=revision&revision=%s',
    'changelog_url': 'http://build.chromium.org/f/chromium/perf/dashboard/ui/changelog_blink.html?url=/trunk&range=%s:%s',
  },
  {
    'name': 'v8',
    'short_name': 'v8',
    'change_url': 'https://code.google.com/p/v8/source/detail?r=%s',
    'changelog_url': 'http://build.chromium.org/f/chromium/perf/dashboard/ui/changelog_v8.html?url=/trunk&range=%s:%s',
  },
  {
    'name': 'nacl',
    'short_name': 'nacl',
    'change_url': 'https://code.google.com/p/nativeclient/source/detail?r=%s',
    // changelog_nacl doesn't actually work yet.
    'changelog_url': 'http://build.chromium.org/f/chromium/perf/dashboard/ui/changelog_nacl.html?url=/trunk&range=%s:%s',
  },
  {
    // Skia, for whatever reason, isn't exposed in the buildbot properties
    // so we never hit this code.
    'name': 'skia',
    'short_name': 'sk',
    'change_url': 'https://code.google.com/p/skia/source/detail?r=%s',
    'changelog_url': 'http://build.chromium.org/f/chromium/perf/dashboard/ui/changelog_skia.html?url=/trunk&range=%s:%s',
  }
]

function entry_by_name(repo_name) {
  for (var i = 0; i < registry.length; ++i) {
    if (registry[i].name == repo_name)
      return registry[i];
  }
  return null;
}

repositories.change_url = function(repo_name, revision) {
  return entry_by_name(repo_name).change_url.replace('%s', revision);
}

repositories.changelog_url = function(repo_name, start, end) {
  return entry_by_name(repo_name).changelog_url.replace('%s', start).replace('%s', end);
}

repositories.short_name = function(repo_name) {
  return entry_by_name(repo_name).short_name;
}

})();