// Needed to make filters visible inside sortable-table.
PolymerExpressions.prototype.since_string = function(value) {
  // Buildbot talks to us in seconds, js expect milliseconds
  date = new Date(value * 1000.0);
  return humanized_time_span(date);
}
PolymerExpressions.prototype.falsy_to_empty = function(value) {
  return value ? value : '';
}
// Display name for the master, does not include chromium.
PolymerExpressions.prototype.master_name = function(value) {
  if (!value)
    return value;
  var url_parts = value.split('/');
  var long_name = url_parts[url_parts.length - 1];
  if (long_name.indexOf('chromium.') == 0) {
    return long_name.slice('chromium.'.length);
  }
  return long_name;
}
PolymerExpressions.prototype.slave_url = function(failure) {
  return failure.master_url + '/buildslaves/' + failure.slave_name;
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

function masterNameFromURL(master_url) {
  var parts = master_url.trimRight('/').split('/')
  return parts[parts.length - 1]
}

PolymerExpressions.prototype.flakiness_dashboard_url = function(test_name, step_name, master_url) {
  if (!test_name)
    return '';
  if (step_name.indexOf('test') == -1)
    return '';
  return "http://test-results.appspot.com/dashboards/flakiness_dashboard.html#"
    + "testType=" + step_name
    + "&tests=" + encodeURIComponent(test_name)
    + "&master=" + masterNameFromURL(master_url);
}
