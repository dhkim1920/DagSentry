// A view owns its main response and all dependent summaries/history requests.
let viewController = new AbortController();
let trendController = null;

export function cancelViewRequests() {
  viewController.abort();
  trendController?.abort();
}

export function currentViewRequest() {
  const controller = viewController;
  return {
    signal: controller.signal,
    isCurrent: () => controller === viewController && !controller.signal.aborted,
  };
}

export function beginViewRequest() {
  cancelViewRequests();
  viewController = new AbortController();
  return currentViewRequest();
}

export function beginTrendRequest() {
  trendController?.abort();
  const controller = new AbortController();
  trendController = controller;
  const view = currentViewRequest();
  return {
    signal: controller.signal,
    isCurrent: () => view.isCurrent() && controller === trendController && !controller.signal.aborted,
  };
}
