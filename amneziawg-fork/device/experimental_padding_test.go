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

func TestSmoothExtraPadding(t *testing.T) {
	for _, test := range []struct {
		name          string
		size, limit   int
		maximumBudget int
	}{
		{"small packet", 64, 1500, 160},
		{"near profile ceiling", 1100, 1500, 50},
		{"lower UDP window", 500, 512, 6},
		{"at profile ceiling", 1200, 1500, 0},
		{"above profile ceiling", 1300, 1500, 0},
		{"above UDP window", 600, 512, 0},
	} {
		t.Run(test.name, func(t *testing.T) {
			if budget := smoothPaddingBudgetFor(test.size, test.limit); budget != test.maximumBudget {
				t.Fatalf("budget = %d, want %d", budget, test.maximumBudget)
			}
			for range 100 {
				padding := smoothExtraPadding(test.size, test.limit)
				if padding < 0 || padding > test.maximumBudget {
					t.Fatalf("padding = %d, want 0..%d", padding, test.maximumBudget)
				}
			}
		})
	}
}

func TestPaddingProfilesOnFixedLengths(t *testing.T) {
	// Synthetic UDP payload lengths after normal AWG padding. Repeat each
	// length equally to compare the profiles on the same input distribution.
	sizes := []int{64, 96, 176, 320, 576, 896, 1152, 1184}
	const samplesPerSize = 4096
	const limit = 1200
	stock := Peer{device: &Device{}}
	stock.udpWindow.Store(limit)
	stock.device.contentPaddingAddition.Store(UintRange(80) << 32) // Upstream range 0–80.
	var adaptive adaptivePaddingBudget
	for _, profile := range []struct {
		name  string
		extra func(int, int) int
	}{
		{"baseline", func(int, int) int { return 0 }},
		{"stock-content-padding-0-80", func(size, _ int) int { return stock.randomPaddingAddition(size) }},
		{"grid", experimentalExtraPadding},
		{"smooth", smoothExtraPadding},
		{"adaptive", adaptive.extra},
	} {
		t.Run(profile.name, func(t *testing.T) {
			var histogram, smallHistogram [limit + 1]int
			var distinct, extraBytes, inputBytes, gridPackets, smallSamples int
			for range samplesPerSize {
				for _, size := range sizes {
					padding := profile.extra(size, limit)
					length := size + padding
					if length < size || length > limit {
						t.Fatalf("length %d outside [%d, %d]", length, size, limit)
					}
					if histogram[length] == 0 {
						distinct++
					}
					histogram[length]++
					if size <= adaptiveSmallPacketLimit {
						smallHistogram[length]++
						smallSamples++
					}
					extraBytes += padding
					inputBytes += size
					if length%experimentalSizeStep < experimentalSizeJitter {
						gridPackets++
					}
				}
			}
			t.Logf("distinct lengths: %d, in grid bands: %.1f%%, added bytes: %.1f%%, same-length pairs: %.2f%% (small: %.2f%%)",
				distinct, 100*float64(gridPackets)/float64(len(sizes)*samplesPerSize),
				100*float64(extraBytes)/float64(inputBytes),
				sameLengthPairPercentage(histogram[:], len(sizes)*samplesPerSize),
				sameLengthPairPercentage(smallHistogram[:], smallSamples))
		})
	}
}

func sameLengthPairPercentage(histogram []int, samples int) float64 {
	var matchingPairs int64
	for _, count := range histogram {
		matchingPairs += int64(count) * int64(count-1)
	}
	return 100 * float64(matchingPairs) / float64(int64(samples)*int64(samples-1))
}
