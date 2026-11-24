// SPDX-License-Identifier: MIT

package device

import "testing"

func TestExperimentalExtraPadding(t *testing.T) {
	for _, test := range []struct {
		name       string
		size       int
		limit      int
		minPadding int
		maxPadding int
	}{
		{"promote to next boundary", 300, 512, 84, 115},
		{"apply jitter at boundary", 384, 415, 0, 31},
		{"skip if jitter would exceed limit", 300, 414, 0, 0},
		{"leave oversized baseline alone", 513, 512, 0, 0},
	} {
		t.Run(test.name, func(t *testing.T) {
			for range 100 {
				padding := experimentalExtraPadding(test.size, test.limit)
				if padding < test.minPadding || padding > test.maxPadding {
					t.Fatalf("padding = %d, want %d..%d", padding, test.minPadding, test.maxPadding)
				}
			}
		})
	}
}
