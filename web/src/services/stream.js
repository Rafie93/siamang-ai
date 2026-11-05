// The backend's event stream. It only says that something changed: state still comes from the HTTP routes.
const EVENTS = ['ready', 'lifecycle', 'execution', 'changed', 'heartbeat', 'ping']
const SILENCE_MS = 25000 // the backend pings every 10 seconds
const RETRY_MIN_MS = 3000
const RETRY_MAX_MS = 60000

/**
 * Opens the stream and keeps it open. `onFrame(type, data)` gets each event; `onLost()` says updates stopped.
 * The browser reconnects by itself after a dropped connection, with the last id, so nothing is missed. It gives up after
 * any error status, such as a session the gateway no longer knows, so that case retries here, slowly.
 * Returns null where EventSource does not exist; the caller keeps polling.
 */
export function openStream({
  onFrame,
  onLost,
  Source = globalThis.EventSource,
  url = '/api/v0/stream',
  silenceMs = SILENCE_MS,
} = {}) {
  if (!Source) return null
  let source,
    silence,
    retry,
    closed = false,
    wait = RETRY_MIN_MS,
    connected = false

  function lost() {
    if (!connected) return
    connected = false
    onLost()
  }
  function again() {
    clearTimeout(silence)
    source?.close()
    lost()
    clearTimeout(retry)
    retry = setTimeout(connect, wait)
    wait = Math.min(wait * 2, RETRY_MAX_MS)
  }
  function heard() {
    clearTimeout(silence)
    silence = setTimeout(again, silenceMs) // a connection can die without anyone saying so
  }
  function connect() {
    if (closed) return
    source = new Source(url)
    heard()
    for (const type of EVENTS)
      source.addEventListener(type, event => {
        let data
        try {
          data = JSON.parse(event.data)
        } catch {
          return
        }
        if (type === 'ready') {
          connected = true
          wait = RETRY_MIN_MS
        }
        heard()
        onFrame(type, data)
      })
    source.onerror = () => {
      if (source.readyState === Source.CLOSED) again()
      else lost() // reconnecting: polling covers the gap until the next `ready`
    }
  }
  connect()
  return {
    close() {
      closed = true
      clearTimeout(silence)
      clearTimeout(retry)
      source?.close()
    },
  }
}
