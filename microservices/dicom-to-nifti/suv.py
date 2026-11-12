"""
Body-weight SUV (SUVbw) scale factors for PET DICOM, following QIBA and Cornerstone's
calculate-suv (https://github.com/cornerstonejs/calculate-suv).

Time handling is taken from petct-automated-workflow/src/microservices/dicom-to-nifti/utils.py
and extended with dates (scans across midnight), DecayCorrection NONE/ADMIN/START and the
Philips CNTS units.

SUV = rescaled pixel value * factor, with one factor per slice (Units and decay correction
can make it slice dependent).
"""
import datetime

import numpy as np


class SUVError(ValueError):
    pass


def parse_time(value):
    """DICOM TM ('HHMMSS', 'HHMMSS.ffffff', 'HHMM', 'HH') -> datetime.time."""
    value = str(value).strip().replace(':', '')
    for fmt in ('%H%M%S.%f', '%H%M%S', '%H%M', '%H'):
        try:
            return datetime.datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    raise SUVError(f'Unrecognized DICOM time: {value!r}')


def parse_date(value):
    value = str(value).strip().replace('.', '').replace('-', '')
    return datetime.datetime.strptime(value[:8], '%Y%m%d').date()


def parse_datetime(value):
    """DICOM DT 'YYYYMMDDHHMMSS.ffffff&ZZXX' -> naive datetime (time zone ignored)."""
    value = str(value).strip()
    for sign in ('+', '-'):
        if sign in value[8:]:
            value = value[:8] + value[8:].split(sign)[0]
    if len(value) < 8:
        raise SUVError(f'Unrecognized DICOM datetime: {value!r}')
    date = parse_date(value[:8])
    time = parse_time(value[8:]) if len(value) > 8 else datetime.time()
    return datetime.datetime.combine(date, time)


def _combine(date_value, time_value):
    date = parse_date(date_value) if date_value else datetime.date(2000, 1, 1)
    return datetime.datetime.combine(date, parse_time(time_value))


def _text(ds, tag):
    if tag not in ds:
        return None
    value = ds[tag].value
    if value in (None, ''):
        return None
    return value.decode(errors='ignore').strip() if isinstance(value, bytes) else str(value).strip()


def _positive_float(ds, tag):
    try:
        value = float(_text(ds, tag))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _radiopharmaceutical(ds):
    sequence = ds.get('RadiopharmaceuticalInformationSequence', None)
    if not sequence:
        raise SUVError('RadiopharmaceuticalInformationSequence is missing')
    return sequence[0]


def _series_date(ds):
    return getattr(ds, 'SeriesDate', None) or getattr(ds, 'AcquisitionDate', None) or getattr(ds, 'StudyDate', None)


def injection_datetime(ds):
    info = _radiopharmaceutical(ds)
    if getattr(info, 'RadiopharmaceuticalStartDateTime', None):
        return parse_datetime(info.RadiopharmaceuticalStartDateTime)
    if getattr(info, 'RadiopharmaceuticalStartTime', None):
        return _combine(_series_date(ds), info.RadiopharmaceuticalStartTime)
    raise SUVError('Injection time (RadiopharmaceuticalStartTime/DateTime) is missing')


def _acquisition_datetime(ds):
    if getattr(ds, 'AcquisitionDateTime', None):
        return parse_datetime(ds.AcquisitionDateTime)
    if getattr(ds, 'AcquisitionTime', None):
        return _combine(getattr(ds, 'AcquisitionDate', None) or _series_date(ds), ds.AcquisitionTime)
    return None


def _ge_post_injection_datetime(ds):
    """GE private tag (0009,100d) 'YYYYMMDDHHMMSS': start of the scan, if present."""
    value = _text(ds, (0x0009, 0x100D))
    if value and len(value) >= 14 and value[:14].isdigit():
        return parse_datetime(value[:14])
    return None


def scan_reference_datetime(headers):
    """
    Decay reference time (QIBA, as in Cornerstone's calculate-suv): SeriesDate/Time if not after
    the earliest acquisition time of the series; otherwise (e.g. GE, where SeriesTime can be a
    later timestamp) the GE post-injection time, else the earliest acquisition time.
    """
    first = headers[0]
    acquisitions = [t for t in (_acquisition_datetime(ds) for ds in headers) if t is not None]
    earliest = min(acquisitions) if acquisitions else None
    series = _combine(_series_date(first), first.SeriesTime) if getattr(first, 'SeriesTime', None) else None
    if series and (earliest is None or series <= earliest):
        return series
    ge = _ge_post_injection_datetime(first)
    if ge:
        return ge
    if earliest:
        return earliest
    raise SUVError('No SeriesTime or AcquisitionTime for decay correction')


def _decay(elapsed_seconds, half_life):
    return float(np.exp(-np.log(2) * elapsed_seconds / half_life))


def _elapsed(later, injection):
    seconds = (later - injection).total_seconds()
    if seconds < 0 and seconds > -86400:
        # time without date (or wrong date) across midnight
        seconds += 86400
    if seconds < 0:
        raise SUVError(f'Scan time {later} is before injection time {injection}')
    return seconds


def _weight_grams(ds, allow_estimates, log):
    weight = _positive_float(ds, 'PatientWeight')
    if weight:
        return weight * 1000
    if allow_estimates:
        log('WARNING: PatientWeight missing, using 80 kg')
        return 80000.0
    raise SUVError('PatientWeight is missing (use --allow-estimates to assume 80 kg)')


def _dose_bq(ds, allow_estimates, log):
    dose = _positive_float(_radiopharmaceutical(ds), 'RadionuclideTotalDose')
    if dose:
        return dose
    if allow_estimates:
        log('WARNING: RadionuclideTotalDose missing, using 450 MBq')
        return 450e6
    raise SUVError('RadionuclideTotalDose is missing (use --allow-estimates to assume 450 MBq)')


def _half_life(ds, log):
    half_life = _positive_float(_radiopharmaceutical(ds), 'RadionuclideHalfLife')
    if half_life:
        return half_life
    log('WARNING: RadionuclideHalfLife missing, using F-18 (6586.2 s)')
    return 6586.2


def _bqml_factors(headers, allow_estimates, log):
    """weight / decayed dose for each slice, for pixel values in Bq/ml."""
    first = headers[0]
    weight = _weight_grams(first, allow_estimates, log)
    dose = _dose_bq(first, allow_estimates, log)
    correction = str(getattr(first, 'DecayCorrection', 'START')).strip().upper() or 'START'

    if correction == 'ADMIN':
        log('DecayCorrection ADMIN: images are decay corrected to the injection time')
        return [weight / dose] * len(headers)

    half_life = _half_life(first, log)
    try:
        injection = injection_datetime(first)
    except SUVError:
        if not allow_estimates:
            raise
        log('WARNING: injection time missing, assuming 75 min uptake')
        return [weight / (dose * _decay(4500, half_life))] * len(headers)

    if correction == 'NONE':
        # not decay corrected: each slice is at its own acquisition time
        factors = []
        for ds in headers:
            acquired = _acquisition_datetime(ds) or scan_reference_datetime(headers)
            factors.append(weight / (dose * _decay(_elapsed(acquired, injection), half_life)))
        log('DecayCorrection NONE: decay corrected per slice to its acquisition time')
        return factors

    if correction != 'START':
        log(f'WARNING: unknown DecayCorrection {correction!r}, treating as START')
    scan = scan_reference_datetime(headers)
    elapsed = _elapsed(scan, injection)
    log(f'uptake time {elapsed / 60:.1f} min, decay factor {_decay(elapsed, half_life):.4f}')
    return [weight / (dose * _decay(elapsed, half_life))] * len(headers)


def suv_factors(headers, allow_estimates=False, log=print):
    """
    One SUVbw factor per slice header (headers in slice order). Units (0054,1001):
      BQML        -> weight / decay-corrected dose
      GML         -> already SUVbw, factor 1
      CNTS        -> Philips SUV Scale Factor (7053,1000), else Activity Concentration
                     Scale Factor (7053,1009) to Bq/ml and then as BQML
    Anything else raises SUVError.
    """
    first = headers[0]
    units = (str(getattr(first, 'Units', '')).strip().upper())
    if not units:
        log('WARNING: Units missing, assuming BQML')
        units = 'BQML'
    corrected = [str(x).upper() for x in (getattr(first, 'CorrectedImage', None) or [])]
    if corrected and 'ATTN' not in corrected:
        log('WARNING: CorrectedImage has no ATTN: not attenuation corrected, SUV is not meaningful')

    if units == 'BQML':
        return _bqml_factors(headers, allow_estimates, log)
    if units == 'GML':
        log('Units GML: values are already SUVbw')
        return [1.0] * len(headers)
    if units == 'CNTS':
        suv_scale = [_positive_float(ds, (0x7053, 0x1000)) for ds in headers]
        if all(suv_scale):
            log(f'Units CNTS: Philips SUV Scale Factor (7053,1000) = {suv_scale[0]}')
            return suv_scale
        activity = [_positive_float(ds, (0x7053, 0x1009)) for ds in headers]
        if all(activity):
            log(f'Units CNTS: Philips Activity Concentration Scale Factor (7053,1009) = {activity[0]}')
            bqml = _bqml_factors(headers, allow_estimates, log)
            return [a * b for a, b in zip(activity, bqml)]
        raise SUVError('Units CNTS without the Philips scale factors (7053,1000)/(7053,1009)')
    raise SUVError(f'Unsupported PET Units {units!r}')
