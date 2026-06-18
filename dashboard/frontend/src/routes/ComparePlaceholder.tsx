import { useSearchParams } from 'react-router-dom'
import { GitCompareArrows } from 'lucide-react'
import { AppShell } from '../components/AppShell'
import { Card } from '../components/ui/Card'

/** Compare page lands in Plan 3; this confirms selection plumbing works today. */
export function ComparePlaceholder() {
  const [params] = useSearchParams()
  const ids = (params.get('ids') ?? '').split(',').filter(Boolean)
  return (
    <AppShell>
      <div className="mx-auto max-w-3xl">
        <Card>
          <div className="flex flex-col items-center gap-3 px-4 py-16 text-center">
            <GitCompareArrows className="text-muted" aria-hidden />
            <p className="font-medium">Run comparison</p>
            <p className="text-sm text-muted">
              Coming in Plan 3. Selected runs:{' '}
              <span className="tnum text-foreground">{ids.length ? ids.join(', ') : 'none'}</span>
            </p>
          </div>
        </Card>
      </div>
    </AppShell>
  )
}
