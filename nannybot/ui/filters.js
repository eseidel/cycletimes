// Needed to make filters visible inside sortable-table.
PolymerExpressions.prototype.since_string = function(value) {
  // Buildbot talks to us in seconds, js expect milliseconds
  date = new Date(value * 1000.0);
  return humanized_time_span(date);
}
PolymerExpressions.prototype.falsy_to_empty = function(value) {
  return value ? value : '';
}
PolymerExpressions.prototype.master_name = function(value) {
  if (!value)
    return 'foo';
  var url_parts = value.split('/');
  var long_name = url_parts[url_parts.length - 1];
  if (long_name.indexOf('chromium.') == 0) {
    return long_name.slice('chromium.'.length);
  }
  return long_name;
}
PolymerExpressions.prototype.builder_url = function(failure) {
  return failure.master_url + '/builders/' + failure.builder_name;
}
PolymerExpressions.prototype.build_url = function(failure, build_number) {
  return PolymerExpressions.prototype.builder_url(failure) + '/builds/' + build_number;
}
PolymerExpressions.prototype.step_url = function(failure, build_number) {
  return PolymerExpressions.prototype.build_url(failure, build_number) + '/steps/' + failure.step_name;
}
PolymerExpressions.prototype.stdio_url = function(failure, build_number) {
  return PolymerExpressions.prototype.step_url(failure, build_number) + '/logs/stdio';
}

// This map is matches the test-results code:
// https://code.google.com/p/chromium/codesearch#chromium/src/third_party/WebKit/Tools/TestResultServer/handlers/buildershandler.py&l=40
var MASTERS = [
    {'name': 'ChromiumWin', 'url_name': 'chromium.win', 'groups': ['@ToT Chromium']},
    {'name': 'ChromiumMac', 'url_name': 'chromium.mac', 'groups': ['@ToT Chromium']},
    {'name': 'ChromiumLinux', 'url_name': 'chromium.linux', 'groups': ['@ToT Chromium']},
    {'name': 'ChromiumChromiumOS', 'url_name': 'chromium.chromiumos', 'groups': ['@ToT ChromeOS']},
    {'name': 'ChromiumGPU', 'url_name': 'chromium.gpu', 'groups': ['@ToT Chromium']},
    {'name': 'ChromiumGPUFYI', 'url_name': 'chromium.gpu.fyi', 'groups': ['@ToT Chromium FYI']},
    {'name': 'ChromiumWebkit', 'url_name': 'chromium.webkit', 'groups': ['@ToT Chromium', '@ToT Blink']},
    {'name': 'ChromiumFYI', 'url_name': 'chromium.fyi', 'groups': ['@ToT Chromium FYI']},
    {'name': 'GpuTryServer', 'url_name': 'tryserver.chromium.gpu', 'groups': ['TryServers']},
    {'name': 'V8', 'url_name': 'client.v8', 'groups': ['@ToT V8']},
];

function masterNameFromURL(master_url) {
  var parts = master_url.trimRight('/').split('/')
  return parts[parts.length - 1]
}

// FIXME: test-results supports multiple 'groups' but it's an unused feature.
// It's not clear that any given waterfall should ever be in more than one 'tree'.
function groupForMaster(master_url) {
  var name = masterNameFromURL(master_url);
  for (var x = 0; x < MASTERS.length; ++x) {
    var record = MASTERS[x];
    if (record['url_name'] == name)
      return record['groups'][0];
  }
}

PolymerExpressions.prototype.flakiness_dashboard_url = function(test_name, step_name, master_url) {
  if (!test_name)
    return '';
  if (step_name.indexOf('test') == -1)
    return '';
  return "http://test-results.appspot.com/dashboards/flakiness_dashboard.html#"
    + "testType=" + step_name
    + "&tests=" + encodeURIComponent(test_name)
    + "&group=" + groupForMaster(master_url);
}
