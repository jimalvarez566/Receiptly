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
            <a
              href={current.link.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-blue-400 underline underline-offset-2 mt-2 inline-block hover:text-blue-300"
            >
              Open in new tab
            </a>
          </div>
        )}
      </div>
    </section>
  )
}
