import { useEffect, useRef, useState } from 'react'
import { api } from '@/lib/api'
import type { ReceiptImageUrl } from '@/lib/api'

type View =
  | { receiptId: number; phase: 'loading' }
  | { receiptId: number; phase: 'ready'; link: ReceiptImageUrl }
  | { receiptId: number; phase: 'error'; message: string }

const FALLBACK_MESSAGE = "Couldn't load the receipt image."

export function ReceiptImage({ receiptId }: { receiptId: number }) {
  const [view, setView] = useState<View>({ receiptId, phase: 'loading' })
  const [attempt, setAttempt] = useState(0)
  // Keyed to the receipt so a stale state from another receipt is ignored.
  const [openState, setOpenState] = useState<{
    receiptId: number
    opening: boolean
    error: string | null
  }>({ receiptId, opening: false, error: null })
  const opening = openState.receiptId === receiptId && openState.opening
  const openError = openState.receiptId === receiptId ? openState.error : null
  // Bumped when the receipt changes or the component unmounts, so an
  // in-flight "open in new tab" can tell it is stale.
  const generation = useRef(0)

  useEffect(() => {
    generation.current += 1
    return () => {
      generation.current += 1
    }
  }, [receiptId])
  // Whether the one automatic refetch (after the browser failed to render the
  // file) has been used for the current receipt.
  const autoRetry = useRef({ receiptId, used: false })

  useEffect(() => {
    let cancelled = false
    api
      .getReceiptImageUrl(receiptId)
      .then((link) => {
        if (!cancelled) setView({ receiptId, phase: 'ready', link })
      })
      .catch((err) => {
        if (cancelled) return
        const message = err instanceof Error && err.message ? err.message : FALLBACK_MESSAGE
        setView({ receiptId, phase: 'error', message })
      })
    return () => {
      cancelled = true
    }
  }, [receiptId, attempt])

  const refetch = () => {
    setView({ receiptId, phase: 'loading' })
    setAttempt((n) => n + 1)
  }

  const handleRenderFailure = () => {
    if (autoRetry.current.receiptId !== receiptId) {
      autoRetry.current = { receiptId, used: false }
    }
    if (!autoRetry.current.used) {
      autoRetry.current.used = true
      refetch()
    } else {
      setView({ receiptId, phase: 'error', message: FALLBACK_MESSAGE })
    }
  }

  const handleManualRetry = () => {
    autoRetry.current = { receiptId, used: false }
    refetch()
  }

  // The signed link on screen expires after a few minutes, so "Open in new tab"
  // fetches a fresh one on click. The blank tab is opened synchronously to
  // avoid popup blockers, then pointed at the fresh link.
  const handleOpenInNewTab = async () => {
    if (opening) return
    const gen = generation.current
    const w = window.open('', '_blank')
    if (!w) {
      setOpenState({
        receiptId,
        opening: false,
        error: 'Your browser blocked the new tab — allow pop-ups and try again.',
      })
      return
    }
    w.opener = null
    setOpenState({ receiptId, opening: true, error: null })
    // Stale request: only clear this receipt's pending flag; never set an
    // error or navigate.
    const clearPending = () =>
      setOpenState((cur) =>
        cur.receiptId === receiptId && cur.opening
          ? { receiptId, opening: false, error: null }
          : cur,
      )
    const closeBlank = () => {
      try {
        w.close()
      } catch {
        // ignore
      }
    }
    try {
      const fresh = await api.getReceiptImageUrl(receiptId)
      if (generation.current !== gen) {
        closeBlank()
        clearPending()
        return
      }
      w.location.href = fresh.url
      setOpenState({ receiptId, opening: false, error: null })
    } catch (err) {
      closeBlank()
      if (generation.current !== gen) {
        clearPending()
        return
      }
      const message = err instanceof Error && err.message ? err.message : FALLBACK_MESSAGE
      setOpenState({ receiptId, opening: false, error: message })
    }
  }

  // A view for a previous receipt counts as loading.
  const current: View = view.receiptId === receiptId ? view : { receiptId, phase: 'loading' }

  return (
    <section className="border border-[#262626] rounded mb-4">
      <div className="px-4 py-2.5 border-b border-[#262626]">
        <h2 className="text-xs font-medium text-[#737373] uppercase tracking-wider">Receipt</h2>
      </div>
      <div className="p-4">
        {current.phase === 'loading' && (
          <p className="text-sm text-[#737373]">Loading image…</p>
        )}

        {current.phase === 'error' && (
          <div>
            <p className="text-sm text-red-400 mb-3">{current.message}</p>
            <button
              onClick={handleManualRetry}
              className="text-xs px-3 py-1.5 rounded border border-[#262626] text-[#737373] hover:text-white hover:border-[#404040] transition-colors"
            >
              Retry
            </button>
          </div>
        )}

        {current.phase === 'ready' && current.link.kind === 'image' && (
          <img
            src={current.link.url}
            alt="Receipt"
            onError={handleRenderFailure}
            className="block max-w-full h-auto rounded"
          />
        )}

        {current.phase === 'ready' && current.link.kind === 'pdf' && (
          <div>
            <object
              type="application/pdf"
              data={current.link.url}
              onError={handleRenderFailure}
              className="w-full rounded bg-[#1a1a1a]"
              style={{ height: 600 }}
            >
              <p className="text-sm text-[#737373]">Can't preview this PDF here.</p>
            </object>
            <button
              type="button"
              onClick={handleOpenInNewTab}
              disabled={opening}
              className="text-xs text-blue-400 underline underline-offset-2 mt-2 inline-block hover:text-blue-300 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Open in new tab
            </button>
            {openError && <p className="text-sm text-red-400 mt-2">{openError}</p>}
          </div>
        )}
      </div>
    </section>
  )
}
