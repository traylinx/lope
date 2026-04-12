# Acknowledgements

## Caveman Mode

Lope's token-efficient validator communication is adapted from
[JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman)
(MIT License, copyright 2026 Julius Brussee).

Core terseness rules, intensity levels, and the "drop articles, keep
code exact" pattern come from upstream. Lope adapts them specifically
for validator prompt/response compression, where every token saved
is multiplied across N validators x M phases x retries.
