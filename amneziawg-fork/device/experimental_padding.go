// SPDX-License-Identifier: MIT

package device

const (
	experimentalSizeStep   = 128
	experimentalSizeJitter = 32
)

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
