// SPDX-License-Identifier: MIT

package device

import (
	"sync"
	"sync/atomic"
	"testing"
)

func TestAdaptivePaddingBudget(t *testing.T) {
	var budget adaptivePaddingBudget
	var input, added int
	for range 4096 {
		for _, size := range []int{64, 96, 320, 576, 1152, 1184, 1250} {
			padding := budget.extra(size, 1200)
			maximum := min(smoothPaddingBudget, max(0, 1200-size))
			if size > adaptiveSmallPacketLimit {
				maximum = min(maximum, adaptiveLargePacketBudget)
			}
			if padding < 0 || padding > maximum {
				t.Fatalf("%d-byte datagram gained %d bytes; want 0..%d", size, padding, maximum)
			}
			input += size
			added += padding
			if added*adaptivePaddingRateDivisor > input {
				t.Fatalf("%d added bytes exceeded budget for %d input bytes", added, input)
			}
		}
	}
}

func TestAdaptivePaddingConcurrentBudget(t *testing.T) {
	const workers, packets, size = 8, 4096, 96
	var budget adaptivePaddingBudget
	var group sync.WaitGroup
	var added atomic.Int64
	for range workers {
		group.Go(func() {
			var total int64
			for range packets {
				total += int64(budget.extra(size, 1200))
			}
			added.Add(total)
		})
	}
	group.Wait()
	if total := added.Load(); total*adaptivePaddingRateDivisor > workers*packets*size {
		t.Fatalf("concurrent workers exceeded shared padding budget: %d bytes", total)
	}
}
