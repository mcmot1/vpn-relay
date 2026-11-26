// SPDX-License-Identifier: MIT

package device

const (
	experimentalSizeStep   = 128
	experimentalSizeJitter = 32
	smoothPaddingCeiling   = 1200 // UDP payload bytes, including AWG overhead.
	smoothPaddingBudget    = 160
)

const (
	paddingProfileBaseline = iota
	paddingProfileGrid
	paddingProfileSmooth
	paddingProfileAdaptive
)

func smoothPaddingBudgetFor(paddedSize, sizeLimit int) int {
	// Taper the range near the ceiling without clipping draws to one size.
	remaining := min(sizeLimit, smoothPaddingCeiling) - paddedSize
	return max(0, min(smoothPaddingBudget, remaining/2))
}

func smoothExtraPadding(paddedSize, sizeLimit int) int {
	budget := smoothPaddingBudgetFor(paddedSize, sizeLimit)
	if budget == 0 {
		return 0
	}
	return int(fastrandn(uint32(budget + 1)))
}

func experimentalExtraPadding(paddedSize, sizeLimit int) int {
	// Group nearby datagram lengths around the next size boundary, with
	// independently sampled jitter on each packet. Skip the promotion if the
	// whole group does not fit, to avoid a pileup at the size limit.
	boundary := (paddedSize + experimentalSizeStep - 1) / experimentalSizeStep * experimentalSizeStep
	if boundary+experimentalSizeJitter-1 > sizeLimit {
		return 0
	}
	return boundary - paddedSize + int(fastrandn(experimentalSizeJitter))
}
