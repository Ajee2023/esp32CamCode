class WebUI {
  #socket;

  constructor(options = {}) {
    this.#socket = io(`http://${window.location.host}`, options);
  }

  on_connect(callback) {
    this.#socket.on('connect', callback);
  }

  on_disconnect(callback) {
    this.#socket.on('disconnect', callback);
  }

  on_message(eventName, callback) {
    this.#socket.on(eventName, callback);
  }

  send_message(eventName, data) {
    this.#socket.emit(eventName, data ?? {});
  }
}
