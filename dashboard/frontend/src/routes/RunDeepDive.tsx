import { useParams } from 'react-router-dom'
import { AppShell } from '../components/AppShell'
import { Card } from '../components/ui/Card'

/** Minimal stub — full Overview/Lineage tabs land in FE Task 4–5. */
export function RunDeepDive() {
  const { id } = useParams<{ id: string }>()
  return (
    <AppShell>
      <div className="mx-auto max-w-6xl">
        <h1 className="text-2xl font-semibold tracking-tight">{id}</h1>
        <Card className="mt-4">
          <div className="p-6 text-sm text-muted">Deep-dive views land in the next tasks.</div>
        </Card>
      </div>
    </AppShell>
  )
}
