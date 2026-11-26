// SPDX-License-Identifier: MIT

package device

import "sync/atomic"

const (
	adaptivePaddingRateDivisor = 12
	adaptivePaddingBurst       = 256
	adaptiveSmallPacketLimit   = 512
	adaptiveLargePacketBudget  = 16
)

type adaptivePaddingBudget struct {
	// One outgoing byte earns one credit. An added byte spends 12 credits.
	credits atomic.Uint32
}

func (budget *adaptivePaddingBudget) extra(paddedSize, sizeLimit int) int {
	maximum := smoothPaddingBudgetFor(paddedSize, sizeLimit)
	if paddedSize > adaptiveSmallPacketLimit {
		maximum = min(maximum, adaptiveLargePacketBudget)
	}

	for {
		previous := budget.credits.Load()
		available := min(previous+uint32(paddedSize), adaptivePaddingBurst*adaptivePaddingRateDivisor)
		limit := min(maximum, int(available/adaptivePaddingRateDivisor))
		padding := fastrandn(uint32(limit + 1))
		remaining := available - padding*adaptivePaddingRateDivisor
		if budget.credits.CompareAndSwap(previous, remaining) {
			return int(padding)
		}
	}
}
