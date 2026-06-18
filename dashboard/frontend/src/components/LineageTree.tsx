import type { RunGraph } from '../lib/types'
import { Card } from './ui/Card'

/** Stub — real best-path spine lands in FE Task 5. */
export function LineageTree({ graph }: { graph: RunGraph }) {
  return (
    <Card>
      <div className="p-8 text-center text-sm text-muted">
        Lineage spine ({graph.nodes.length} candidates) — rendering lands in the next task.
      </div>
    </Card>
  )
}
