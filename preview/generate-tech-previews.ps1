param(
  [string]$InputPath = "C:\Users\乔远\AppData\Local\Temp\codex-clipboard-be40fc0a-3ebe-4e07-b399-f129b633c07c.png",
  [string]$OutputDir = "C:\Users\乔远\Desktop\tender_agent\preview"
)

Add-Type -AssemblyName System.Drawing

function Clamp([double]$value) {
  if ($value -lt 0) { return 0.0 }
  if ($value -gt 1) { return 1.0 }
  return $value
}

function Mix-Color([System.Drawing.Color]$from, [System.Drawing.Color]$to, [double]$amount) {
  $t = Clamp $amount
  return [System.Drawing.Color]::FromArgb(
    [int](255 * (1 - $t) + $to.R * $t),
    [int]($from.G * (1 - $t) + $to.G * $t),
    [int]($from.B * (1 - $t) + $to.B * $t)
  )
}

function Hsl([System.Drawing.Color]$color) {
  $r = $color.R / 255.0
  $g = $color.G / 255.0
  $b = $color.B / 255.0
  $max = [Math]::Max($r, [Math]::Max($g, $b))
  $min = [Math]::Min($r, [Math]::Min($g, $b))
  $l = ($max + $min) / 2.0
  if ($max -eq $min) {
    return @{ H = 0.0; S = 0.0; L = $l }
  }
  $d = $max - $min
  $s = if ($l -gt 0.5) { $d / (2 - $max - $min) } else { $d / ($max + $min) }
  if ($max -eq $r) {
    $h = ($g - $b) / $d + $(if ($g -lt $b) { 6 } else { 0 })
  } elseif ($max -eq $g) {
    $h = ($b - $r) / $d + 2
  } else {
    $h = ($r - $g) / $d + 4
  }
  return @{ H = $h / 6.0; S = $s; L = $l }
}

function New-Palette(
  [string]$Name,
  [System.Drawing.Color]$Page,
  [System.Drawing.Color]$Surface,
  [System.Drawing.Color]$Panel,
  [System.Drawing.Color]$Border,
  [System.Drawing.Color]$Text,
  [System.Drawing.Color]$Muted,
  [System.Drawing.Color]$Primary,
  [System.Drawing.Color]$Success,
  [System.Drawing.Color]$Warning,
  [System.Drawing.Color]$Danger
) {
  return [pscustomobject]@{
    Name = $Name
    Page = $Page
    Surface = $Surface
    Panel = $Panel
    Border = $Border
    Text = $Text
    Muted = $Muted
    Primary = $Primary
    Success = $Success
    Warning = $Warning
    Danger = $Danger
  }
}

$palettes = @()
$palettes += New-Palette -Name "tech-carbon-cyan" `
  -Page ([System.Drawing.Color]::FromArgb(8, 16, 28)) `
  -Surface ([System.Drawing.Color]::FromArgb(15, 27, 43)) `
  -Panel ([System.Drawing.Color]::FromArgb(19, 37, 57)) `
  -Border ([System.Drawing.Color]::FromArgb(39, 70, 91)) `
  -Text ([System.Drawing.Color]::FromArgb(220, 238, 249)) `
  -Muted ([System.Drawing.Color]::FromArgb(126, 158, 181)) `
  -Primary ([System.Drawing.Color]::FromArgb(38, 213, 255)) `
  -Success ([System.Drawing.Color]::FromArgb(41, 226, 181)) `
  -Warning ([System.Drawing.Color]::FromArgb(255, 185, 76)) `
  -Danger ([System.Drawing.Color]::FromArgb(255, 91, 116))
$palettes += New-Palette -Name "tech-terminal-lime" `
  -Page ([System.Drawing.Color]::FromArgb(9, 17, 22)) `
  -Surface ([System.Drawing.Color]::FromArgb(16, 28, 34)) `
  -Panel ([System.Drawing.Color]::FromArgb(23, 41, 44)) `
  -Border ([System.Drawing.Color]::FromArgb(48, 77, 76)) `
  -Text ([System.Drawing.Color]::FromArgb(232, 244, 235)) `
  -Muted ([System.Drawing.Color]::FromArgb(139, 170, 151)) `
  -Primary ([System.Drawing.Color]::FromArgb(177, 239, 73)) `
  -Success ([System.Drawing.Color]::FromArgb(48, 226, 166)) `
  -Warning ([System.Drawing.Color]::FromArgb(255, 200, 85)) `
  -Danger ([System.Drawing.Color]::FromArgb(255, 100, 120))
$palettes += New-Palette -Name "tech-cobalt-steel" `
  -Page ([System.Drawing.Color]::FromArgb(10, 15, 30)) `
  -Surface ([System.Drawing.Color]::FromArgb(22, 29, 49)) `
  -Panel ([System.Drawing.Color]::FromArgb(29, 39, 65)) `
  -Border ([System.Drawing.Color]::FromArgb(52, 66, 105)) `
  -Text ([System.Drawing.Color]::FromArgb(232, 237, 255)) `
  -Muted ([System.Drawing.Color]::FromArgb(139, 157, 198)) `
  -Primary ([System.Drawing.Color]::FromArgb(91, 140, 255)) `
  -Success ([System.Drawing.Color]::FromArgb(54, 214, 194)) `
  -Warning ([System.Drawing.Color]::FromArgb(255, 179, 71)) `
  -Danger ([System.Drawing.Color]::FromArgb(255, 107, 154))
$palettes += New-Palette -Name "tech-industrial-orange" `
  -Page ([System.Drawing.Color]::FromArgb(20, 15, 13)) `
  -Surface ([System.Drawing.Color]::FromArgb(32, 25, 21)) `
  -Panel ([System.Drawing.Color]::FromArgb(47, 35, 28)) `
  -Border ([System.Drawing.Color]::FromArgb(87, 61, 44)) `
  -Text ([System.Drawing.Color]::FromArgb(250, 239, 226)) `
  -Muted ([System.Drawing.Color]::FromArgb(191, 157, 130)) `
  -Primary ([System.Drawing.Color]::FromArgb(255, 151, 61)) `
  -Success ([System.Drawing.Color]::FromArgb(53, 222, 190)) `
  -Warning ([System.Drawing.Color]::FromArgb(255, 193, 74)) `
  -Danger ([System.Drawing.Color]::FromArgb(255, 100, 89))

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$source = [System.Drawing.Bitmap]::new($InputPath)
$sourceData = $source.LockBits(
  [System.Drawing.Rectangle]::new(0, 0, $source.Width, $source.Height),
  [System.Drawing.Imaging.ImageLockMode]::ReadOnly,
  [System.Drawing.Imaging.PixelFormat]::Format32bppArgb
)
$sourceStride = $sourceData.Stride
$sourceBytes = New-Object byte[] ($sourceStride * $source.Height)
[System.Runtime.InteropServices.Marshal]::Copy($sourceData.Scan0, $sourceBytes, 0, $sourceBytes.Length)
$source.UnlockBits($sourceData)

try {
  foreach ($palette in $palettes) {
    $target = [System.Drawing.Bitmap]::new($source.Width, $source.Height)
    try {
      $targetData = $target.LockBits(
        [System.Drawing.Rectangle]::new(0, 0, $target.Width, $target.Height),
        [System.Drawing.Imaging.ImageLockMode]::WriteOnly,
        [System.Drawing.Imaging.PixelFormat]::Format32bppArgb
      )
      $targetStride = $targetData.Stride
      $targetBytes = New-Object byte[] ($targetStride * $target.Height)
      for ($y = 0; $y -lt $source.Height; $y++) {
        for ($x = 0; $x -lt $source.Width; $x++) {
          $offset = $y * $sourceStride + $x * 4
          $pixel = [System.Drawing.Color]::FromArgb(
            $sourceBytes[$offset + 3],
            $sourceBytes[$offset + 2],
            $sourceBytes[$offset + 1],
            $sourceBytes[$offset]
          )
          if ($y -lt 100) {
            $targetBytes[$offset] = $pixel.B
            $targetBytes[$offset + 1] = $pixel.G
            $targetBytes[$offset + 2] = $pixel.R
            $targetBytes[$offset + 3] = $pixel.A
            continue
          }

          $hsl = Hsl $pixel
          $h = $hsl.H
          $s = $hsl.S
          $l = $hsl.L
          $mapped = $palette.Page

          if ($s -lt 0.12) {
            if ($l -gt 0.975) {
              $mapped = $palette.Surface
            } elseif ($l -gt 0.91) {
              $mapped = $palette.Page
            } elseif ($l -gt 0.78) {
              $mapped = $palette.Panel
            } elseif ($l -gt 0.60) {
              $mapped = $palette.Border
            } elseif ($l -gt 0.35) {
              $mapped = $palette.Muted
            } else {
              $mapped = $palette.Text
            }
          } else {
            $isGreen = ($h -ge 0.20 -and $h -lt 0.50)
            $isWarm = ($h -lt 0.16 -or $h -ge 0.93)
            $isBlue = ($h -ge 0.50 -and $h -lt 0.82)

            if ($l -lt 0.43) {
              $mapped = $palette.Text
            } elseif ($isGreen) {
              $mapped = $palette.Success
            } elseif ($isWarm) {
              $mapped = if ($l -lt 0.58) { $palette.Danger } else { $palette.Warning }
            } elseif ($isBlue) {
              $mapped = $palette.Primary
            } else {
              $mapped = $palette.Muted
            }
          }

          $edgeBlend = if ($s -lt 0.05) { 0.0 } else { [Math]::Min(0.22, $s * 0.22) }
          $final = if ($edgeBlend -gt 0) { Mix-Color $mapped $palette.Panel $edgeBlend } else { $mapped }
          $targetBytes[$offset] = $final.B
          $targetBytes[$offset + 1] = $final.G
          $targetBytes[$offset + 2] = $final.R
          $targetBytes[$offset + 3] = 255
        }
      }
      [System.Runtime.InteropServices.Marshal]::Copy($targetBytes, 0, $targetData.Scan0, $targetBytes.Length)
      $target.UnlockBits($targetData)
      $outPath = Join-Path $OutputDir "$($palette.Name).png"
      $target.Save($outPath, [System.Drawing.Imaging.ImageFormat]::Png)
    } finally {
      $target.Dispose()
    }
  }
} finally {
  $source.Dispose()
}
