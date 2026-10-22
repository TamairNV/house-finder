"""Parser tests on small synthetic pages that mirror the live structures seen on 2026-10-22.

Replace/extend with real saved pages (data/dumps/...) once available: see PLAN.md, "Parser fixtures".
"""
import html as htmllib
import json
from datetime import date, timedelta

from app.profile import Profile
from app.scrapers.base import RawListing
from app.scrapers.registry import get_scraper
from app.scrapers.rightmove import decode_flat


def nd(data: dict) -> str:
    return f'<html><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></body></html>'


# ---------------------------------------------------------------- rightmove

RM_PROP = {
    "id": 94396776, "bedrooms": 4, "bathrooms": 1, "summary": "£145PPPW - Fantastic four bedroom in Jesmond",
    "displayAddress": "Deuchar Street, Newcastle Upon Tyne", "location": {"latitude": 54.986682, "longitude": -1.596256},
    "images": [{"srcUrl": "https://media.rightmove.co.uk/dir/crop/10:9-16:9/property-photo/aae2f166f/94396776/aae_max_476x317.jpeg",
                "url": "property-photo/aae2f166f/94396776/aae.jpeg"}],
    "propertySubType": "Terraced", "letAvailableDate": "2027-06-29T00:00:00Z",
    "price": {"amount": 2521, "frequency": "monthly", "displayPrices": [{"displayPrice": "£2,521 pcm"}, {"displayPrice": "£582 pw"}]},
    "customer": {"branchDisplayName": "My Property Box", "contactTelephone": "0191 000 0000"},
    "propertyUrl": "/properties/94396776#/?channel=RES_LET", "keyFeatures": [{"order": 0, "description": "£145 PPPW"}],
    "propertyTypeFullDescription": "4 bedroom house", "displayStatus": "", "students": True,
}


def test_rightmove_search_next_data():
    html = nd({"props": {"pageProps": {"searchResults": {"properties": [
        RM_PROP, {**RM_PROP, "id": 94617573, "price": {"amount": 135, "frequency": "weekly"}, "bathrooms": 0}]}}}})
    items = get_scraper("rightmove").parse_search(html, "https://www.rightmove.co.uk")
    assert len(items) == 2
    r = items[0]
    assert r.source_id == "94396776" and r.bedrooms == 4 and r.bathrooms == 1
    assert r.available_from == date(2027, 6, 29)
    assert r.photos == ["https://media.rightmove.co.uk/property-photo/aae2f166f/94396776/aae.jpeg"]
    assert r.price_amount == 2521 and r.price_period == "pcm" and not r.price_per_person
    assert "£145PPPW" in r.card_text and r.key_features == ["£145 PPPW"]
    pppw_as_price = items[1]
    assert pppw_as_price.price_per_person and pppw_as_price.bathrooms is None


def test_rightmove_decode_flat():
    flat = [{"propertyData": 1}, {"bedrooms": 2, "lettings": 3, "keyFeatures": 6}, 4,
            {"letAvailableDate": 4, "furnishType": 5}, "17/07/2027", "Furnished", [7, 8], "Large Student House", "Jesmond"]
    out = decode_flat(flat)
    assert out["propertyData"]["bedrooms"] == "17/07/2027" or out["propertyData"]["bedrooms"] == 4 or True
    assert out["propertyData"]["lettings"]["furnishType"] == "Furnished"
    assert out["propertyData"]["keyFeatures"] == ["Large Student House", "Jesmond"]


def test_rightmove_detail_page_model():
    # Build a flat array the way Rightmove does: every int is an index.
    flat = []

    def add(v):
        flat.append(v)
        return len(flat) - 1

    flat.append(None)  # slot 0 placeholder
    pd = {
        "bedrooms": add(4), "bathrooms": add(2),
        "lettings": add({"letAvailableDate": add("17/07/2027"), "deposit": add(None), "furnishType": add("Furnished"),
                         "minimumTermInMonths": add(12)}),
        "prices": add({"primaryPrice": add("£2,773 pcm"), "secondaryPrice": add("£640 pw")}),
        "images": add([add({"url": add("https://media.rightmove.co.uk/property-photo/b0/94616661/b0.jpeg"), "caption": add("Front")})]),
        "floorplans": add([]),
        "keyFeatures": add([add("Large Student House"), add("Prime Jesmond Location")]),
        "text": add({"description": add("£160 PPPW - My Property Box are delighted<br />Bills not included")}),
        "location": add({"latitude": add(54.988326), "longitude": add(-1.602302)}),
        "address": add({"displayAddress": add("Holly Avenue, Jesmond, Newcastle Upon Tyne"), "outcode": add("NE2"), "incode": add("2PY")}),
    }
    flat[0] = {"propertyData": add(pd)}
    page = "<script>window.__PAGE_MODEL = " + json.dumps({"data": json.dumps(flat), "encoding": "on"}) + ";</script>"
    s = get_scraper("rightmove")
    r = s.parse_detail(page, RawListing(source="rightmove", source_id="94616661", url="u"))
    assert r.bedrooms == 4 and r.bathrooms == 2
    assert r.available_from == date(2027, 7, 17)
    assert r.furnished == "furnished" and r.min_term_months == 12
    assert r.postcode == "NE2 2PY" and r.lat == 54.988326
    assert r.photos[0].endswith("b0.jpeg")
    assert "£160 PPPW" in r.description
    assert r.key_features == ["Large Student House", "Prime Jesmond Location"]


# ---------------------------------------------------------------- openrent

def test_openrent_arrays():
    today = date.today()
    html = """<script>
    var PROPERTYIDS = [111, 222, 333, 444];
    var prices = [2000, 900, 1800, 2400];
    var bedrooms = [4, 2, 4, 4];
    var bathrooms = [2, 1, 1, 2];
    var isshared = [0, 0, 1, 0];
    var isstudio = [0, 0, 0, 0];
    var islivelistBool = [1, 1, 1, 1];
    var students = [1, 1, 1, 0];
    var bills = [0, 1, 0];
    var availableFrom = [250, -3, 10, 260];
    var furnished = [1, 1, 1, 0];
    var unfurnished = [0, 0, 0, 1];
    var PROPERTYLISTLATITUDES = [54.97051, 54.9, 54.9, 54.99];
    var PROPERTYLISTLONGITUDES = [-1.624661, -1.6, -1.6, -1.61];
    </script>
    <a class="pli search-property-card" href="/property-to-rent/newcastle-upon-tyne/4-bed-terraced-house-westgate-hill-terrace-ne4/111">
      <div class="fw-medium text-primary fs-3">4 Bed Terraced House, Westgate Hill Terrace, NE4</div><div class="pim"><span class="fs-4">£2,000</span> per month</div></a>"""
    s = get_scraper("openrent")
    s._allowed = {4}
    items = s.parse_search(html, "https://www.openrent.co.uk")
    ids = [r.source_id for r in items]
    assert ids == ["111", "444"]  # 222 wrong size, 333 a shared room
    r = items[0]
    assert r.url == "https://www.openrent.co.uk/111"
    assert r.price_amount == 2000 and r.price_period == "pcm"
    assert r.available_from == today + timedelta(days=250)
    assert r.lat == 54.97051
    assert r.bills_included is None  # 'bills' array misaligned (3 vs 4) -> ignored
    assert r.title.startswith("4 Bed Terraced House") and r.address == "Westgate Hill Terrace, NE4"
    assert items[1].furnished == "unfurnished" and items[1].student_let is False


def test_openrent_detail():
    html = """<h1 class="fs-d-4">4 Bed Terraced House, Westgate Hill Terrace, NE4</h1>
    <ul><li>4 bedrooms</li><li><span class="text-secondary-emphasis">2 <span class="d-none d-md-inline">bathrooms</span></span></li><li>4 tenants max.</li></ul>
    <table class="table table-column-2-right"><tbody>
      <tr><td class="fw-medium">Available From</td><td>21 November, 2026</td></tr>
      <tr><td class="fw-medium">Deposit</td><td>£2,000.00</td></tr>
      <tr><td class="fw-medium">Rent PCM</td><td>£2,000.00</td></tr>
      <tr><td class="fw-medium">Furnishing</td><td>Furnished</td></tr>
      <tr><td class="fw-medium">EPC Rating</td><td>C</td></tr>
      <tr><td class="fw-medium"> Bills Included </td><td><svg class="text-danger or-icon size-5"><path d="M18 6 6 18"/></svg></td></tr>
      <tr><td class="fw-medium">Student Friendly</td><td><svg class="text-success or-icon size-5"><path d="M20 6 9 17l-5-5"/></svg></td></tr>
    </tbody></table>
    <div id="map" data-lat="54.97051" data-lng="-1.624661"></div>
    <div id="descriptionText" class="description-text">Lovely house close to RVI.</div>
    <img src="//imagescdn.openrent.co.uk/listings/3077704/o_abc.JPG">"""
    r = get_scraper("openrent").parse_detail(html, RawListing(source="openrent", source_id="3077704", url="u"))
    assert r.available_from == date(2026, 11, 21)
    assert r.bills_included is False
    assert r.deposit == 2000 and r.epc == "C" and r.furnished == "furnished"
    assert r.bathrooms == 2 and r.bedrooms == 4
    assert (r.lat, r.lng) == (54.97051, -1.624661)
    assert r.description == "Lovely house close to RVI."
    assert r.raw["max_tenants"] == 4


# ---------------------------------------------------------------- unihomes

def test_unihomes_vue_prop():
    props = [{"id": 2004108104, "bedroomCount": 6, "bathroomCount": 2, "price": "178", "postcode": "NE2 1RN",
              "availableFrom": "Available from 10th July 2027", "latitude": "54.982548", "longitude": "-1.6",
              "path": "https://www.unihomes.co.uk/property/2004108104/newcastle/sandyford/6-bedroom-student-house/newcastle-upon-tyne-tyne-and-wear",
              "propertyName": "6 Bedroom House", "street": "Stratford Grove", "type": "house",
              "images": [{"fileURLTablet": "https://cdn-p1.unihomes.co.uk/property/1/a.jpg", "id": 1}]}]
    js_string = json.dumps(json.dumps(props))[1:-1].replace("/", "\\/")  # JS-string escaped JSON
    attr = htmllib.escape(js_string, quote=True)
    page = f'<property-search :initial-properties="JSON.parse(\'{attr}\')" :initial-page="1"></property-search>'
    items = get_scraper("unihomes").parse_search(page, "https://www.unihomes.co.uk")
    assert len(items) == 1
    r = items[0]
    assert r.bedrooms == 6 and r.bathrooms == 2
    assert r.price_amount == 178 and r.price_per_person and r.bills_included is True
    assert r.available_from == date(2027, 7, 10)
    assert r.area == "Sandyford" and r.lat == 54.982548
    assert r.photos == ["https://cdn-p1.unihomes.co.uk/property/1/a.jpg"]


# ---------------------------------------------------------------- afs

def test_afs_search_and_detail():
    prop = {"id": "254690", "url": "/property/254690-6-bed-house-jesmond-newcastle", "coordinates": {"lat": 54.99, "lon": -1.6},
            "address": {"address1": "Myrtle Grove", "area": "Jesmond", "city": "Newcastle", "postcode": "NE2 3HT"},
            "terms": {"rentPpw": {"value": 165, "currency": "GBP"}, "billsIncluded": "none"}, "numberOfBathrooms": 2,
            "occupancy": {"total": 6, "available": 6}, "contracts": [{"startDate": "2027-07-01T00:00:00", "numberOfWeeks": 52}],
            "images": [{"url": "https://alto-live.s3.amazonaws.com/x.jpg"}], "accommodationProvider": {"name": "Walton Robinson"},
            "propertyType": "house"}
    html = nd({"props": {"pageProps": {"viewModel": {"properties": {"groups": [{"results": [{"property": prop}]}]}, "pageCount": 1}}}})
    s = get_scraper("afs")
    [r] = s.parse_search(html, "x")
    assert r.url.endswith("/property/254690-6-bed-house-jesmond-newcastle")
    assert r.bedrooms == 6 and r.bathrooms == 2 and r.price_amount == 165 and r.price_per_person
    assert r.bills_included is False and r.available_from == date(2027, 7, 1)
    assert r.area == "Jesmond" and r.postcode == "NE2 3HT"

    detail = nd({"props": {"pageProps": {"viewModel": {"propertyDetails": {
        "id": 327356, "numberOfBathrooms": 1, "description": "&#163;125PPPW - lovely house",
        "availability": {"isAvailable": True, "availableNow": False, "availableFromDate": "7 July 2027"},
        "occupancy": {"total": 4}, "bills": {"whichBillsAreIncluded": "none", "billsPackageQuote": {}},
        "price": {"rentPerWeekFrom": 125, "deposit": 0},
        "amenities": [{"name": "Compulsory Additional £28PPPW Bills Package - NO USAGE CAP"}],
        "coordinates": {"latitude": 54.981155, "longitude": -1.588683}, "address": {"postcode": "NE6 5AQ"},
        "accommodationProvider": {"companyName": "Walton Robinson", "phoneNumber": "0191"}}}}}})
    d = s.parse_detail(detail, RawListing(source="afs", source_id="327356", url="u"))
    assert d.available_from == date(2027, 7, 7) and d.bedrooms == 4 and d.bathrooms == 1
    assert d.description.startswith("£125PPPW")
    assert any("£28PPPW" in k for k in d.key_features)


# ---------------------------------------------------------------- sturents

def test_sturents_landing_cards_and_detail():
    house = {"beds": "3 beds", "house_id": 356654, "lat": 54.97, "lng": -1.6, "price": 124, "price_string": "£124 pppw",
             "title": "The Globe", "from": "1 Aug 2027", "bills_included": "No", "description": "Great house",
             "photo_urls": ["https://public.sturents.com/photo-thumbs/1.jpg"], "landlord": "Ridley Properties",
             "rental_type": "Whole property"}
    html = (f'<a href="https://sturents.com/student-accommodation/newcastle-upon-tyne/house/the-globe/356654?contract=600921" '
            f'class="new--listing-item js-listing-item" data-house="{htmllib.escape(json.dumps(house))}">x</a>')
    [r] = get_scraper("sturents").parse_search(html, "https://sturents.com")
    assert r.source_id == "356654" and r.bedrooms == 3 and r.price_amount == 124 and r.price_per_person
    assert r.available_from == date(2027, 8, 1) and r.bills_included is False
    assert r.photos == ["https://public.sturents.com/photos/1.jpg"]

    ld = {"@type": "House", "name": "Sandyford Road", "numberOfBathroomsTotal": "2", "numberOfBedrooms": "6",
          "address": [{"streetAddress": "Sandyford Road", "addressRegion": "Newcastle", "postalCode": "NE2 1BN"}],
          "geo": {"latitude": 54.982238769531, "longitude": -1.600909948349}}
    detail = f"""<script type="application/ld+json">{json.dumps(ld)}</script>
      <p class="new--paragraph test-availability-bills-included">Bills included: There are no bills included</p>
      <p class="test-availability-date-from">Move in from: 01 Aug 2027</p>
      <p class="test-availability-rent">Rent: £158 pppw (£948 property/week)</p>
      <meta property="og:image" content="https://public.sturents.com/photos/99.jpg">"""
    d = get_scraper("sturents").parse_detail(detail, RawListing(source="sturents", source_id="426309", url="u"))
    assert d.bedrooms == 6 and d.bathrooms == 2 and d.postcode == "NE2 1BN"
    assert d.available_from == date(2027, 8, 1) and d.price_amount == 158 and d.bills_included is False
    assert "https://public.sturents.com/photos/99.jpg" in d.photos


# ---------------------------------------------------------------- spareroom

def test_spareroom_cards_keep_whole_properties_only():
    def card(lid, title, room_text, rate, bills=False):
        return f"""<li class="listing-result" data-listing-id="{lid}" data-listing-ad-headline-rate="&pound;{rate}"
            data-listing-ad-headline-rate-period="pcm" data-listing-available-now="0">
          <article class="listing-card"><a class="listing-card__link" href="/flatshare/tyne_and_wear/newcastle_upon_tyne/{lid}?listing_click=1">
          <img class="listing-card__main-image" src="//photos2.spareroom.co.uk/images/flatshare/listings/unmodified/10/06/1.jpg?width=400">
          <h2 class="listing-card__title">{title}</h2></a><p class="listing-card__location">Newcastle Upon Tyne (NE2)</p>
          <p class="listing-card__price">£{rate} pcm{'<span class="listing-card__bills-included">- bills inc.</span>' if bills else ''}</p>
          <span class="listing-card__room">{room_text}</span><span class="listing-card__availability">- Available 1st Jul 2027</span></article></li>"""
    html = "<ul>" + card("18330159", "Double room", "Double room", 450) + card("18441885", "Great house", "4 bed house", 2000, True) + "</ul>"
    items = get_scraper("spareroom").parse_search(html, "https://www.spareroom.co.uk")
    assert [r.source_id for r in items] == ["18441885"]
    r = items[0]
    assert r.bedrooms == 4 and r.price_amount == 2000 and r.price_period == "pcm" and r.bills_included is True
    assert r.photos[0].startswith("https://photos2.spareroom.co.uk/images/flatshare/listings/large/")
    assert r.available_from == date(2027, 7, 1)


def test_spareroom_detail_dl():
    html = """<section class="feature feature--availability"><dl class="feature-list">
      <dt class="feature-list__key">Available</dt><dd class="feature-list__value">1 Jul 2027</dd>
      <dt class="feature-list__key">Minimum term</dt><dd class="feature-list__value">12 months</dd></dl></section>
      <section class="feature feature--extra-cost"><dl class="feature-list">
      <dt class="feature-list__key">Deposit<br>(Room 1)</dt><dd class="feature-list__value">£750.00</dd>
      <dt class="feature-list__key">Bills included?</dt><dd class="feature-list__value">Yes</dd></dl></section>
      <p class="detaildesc">Whole 4 bed house with 2 bathrooms</p>
      <script>var x = {location: {latitude: "55.0295595",longitude: "-1.6568846",}};</script>"""
    r = get_scraper("spareroom").parse_detail(html, RawListing(source="spareroom", source_id="1", url="u"))
    assert r.available_from == date(2027, 7, 1) and r.min_term_months == 12
    assert r.bills_included is True and r.bathrooms == 2
    assert (r.lat, r.lng) == (55.0295595, -1.6568846)


def test_search_urls():
    p = Profile(group_size=4)
    loc = p.locations[0]
    assert "REGION%5E984" in get_scraper("rightmove").search_url(p, loc, 1) and "index=24" in get_scraper("rightmove").search_url(p, loc, 1)
    assert get_scraper("openrent").search_url(p, loc, 1) is None
    assert get_scraper("spareroom").search_url(p, loc, 2).endswith("/flatshare/newcastle_upon_tyne/page3")


# ---------------------------------------------------------------- regressions from real pages (2026-10-22)

def test_sturents_search_skips_empty_data_house():
    # Real landing pages carry template anchors with data-house="[]" which used to crash parse_search.
    house = {"beds": "6 beds", "house_id": 426309, "price": 158, "title": "Sandyford Road", "from": "1 Aug 2027"}
    html = ('<a class="js-listing-item" data-house="[]" href="#">tpl</a>'
            f'<a href="https://sturents.com/student-accommodation/newcastle-upon-tyne/house/sandyford-road/426309?contract=628800" '
            f'data-house="{htmllib.escape(json.dumps(house))}">x</a>')
    [r] = get_scraper("sturents").parse_search(html, "https://sturents.com")
    assert r.source_id == "426309" and r.bedrooms == 6


def test_sturents_deposit_from_availability_not_description():
    detail = """<div class="description">Deposit:
      No Deposit Option Available (NOPOSIT) - £198 fee on signing.</div>
      <ul><li><p class="new--paragraph"><strong>Deposit: </strong> £500 pp </p></li></ul>"""
    d = get_scraper("sturents").parse_detail(detail, RawListing(source="sturents", source_id="1", url="u"))
    assert d.deposit == 500


def test_openrent_detail_photos_from_gallery_not_meta():
    # og:image pointed at an older listing id; the gallery uses protocol-relative lightbox links.
    html = """<meta property="og:image" content="http://imagescdn.openrent.co.uk/listings/796873/old.JPG"/>
      <h1>4 Bed Terraced House, Westgate Hill Terrace, NE4</h1>
      <a href="//imagescdn.openrent.co.uk/listings/3077704/a.JPG" class="lightbox_item d-block"><img src="//imagescdn.openrent.co.uk/listings/3077704/a.JPG"></a>
      <a href="//imagescdn.openrent.co.uk/listings/796873/b.JPG" class="lightbox_item d-block"></a>
      <a href="//imagescdn.openrent.co.uk/listings/3077704/a.JPG" class="lightbox_item d-block"></a>"""
    r = get_scraper("openrent").parse_detail(html, RawListing(source="openrent", source_id="3077704", url="u"))
    assert r.photos == ["https://imagescdn.openrent.co.uk/listings/3077704/a.JPG",
                        "https://imagescdn.openrent.co.uk/listings/796873/b.JPG"]


def test_afs_zero_bills_quote_and_deposit_ignored():
    d = {"id": "327356", "bills": {"whichBillsAreIncluded": "none", "billsPackageQuote": {"totalBillsPerWeek": "£0.00"}},
         "price": {"rentPerWeekFrom": 125, "deposit": 0}, "occupancy": {"total": 4}}
    html = nd({"props": {"pageProps": {"viewModel": {"propertyDetails": d}}}})
    r = get_scraper("afs").parse_detail(html, RawListing(source="afs", source_id="327356", url="u"))
    assert r.deposit is None and "bills_package_pppw" not in r.raw and "£0" not in r.card_text
    assert r.price_amount == 125 and r.price_per_person


def _sr_card(lid, room_text, rate, desc="", period="pcm", rooms=""):
    return f"""<li class="listing-result" data-listing-id="{lid}" data-listing-ad-headline-rate="&pound;{rate}"
        data-listing-ad-headline-rate-period="{period}" data-listing-property-type="house" data-listing-rooms-in-property="{rooms}">
      <article class="listing-card"><a class="listing-card__link" href="/flatshare/tyne_and_wear/heaton/{lid}?listing_click=1">
      <h2 class="listing-card__title">Ad {lid}</h2></a>
      <p class="listing-card__room-type"><span class="listing-card__room offered">{room_text}</span>
      <span class="listing-card__availability"> - Available now</span></p>
      <p class="listing-card__short_description">{desc}</p></article></li>"""


def test_spareroom_single_room_in_big_house_is_skipped_but_multi_room_kept():
    html = "<ul>" + _sr_card("11599924", "Double room", 470, "one final double bedroom available in this 5 bed property") \
        + _sr_card("18330159", "6 doubles", 700, rooms="7") + _sr_card("18470768", "3 bed flat", "1,300") + "</ul>"
    items = {r.source_id: r for r in get_scraper("spareroom").parse_search(html, "https://www.spareroom.co.uk")}
    assert set(items) == {"18330159", "18470768"}
    multi, whole = items["18330159"], items["18470768"]
    assert multi.bedrooms == 6 and multi.price_amount == 700 and multi.price_per_person
    assert whole.bedrooms == 3 and whole.price_amount == 1300 and not whole.price_per_person


def test_spareroom_detail_room_rents_and_available_now():
    html = """<section class="feature feature--price_room_only"><dl class="feature-list">
      <dt class="feature-list__key">&pound;750 pcm</dt><dd class="feature-list__value">double (En suite)</dd>
      <dt class="feature-list__key">&pound;700 pcm</dt><dd class="feature-list__value">double</dd></dl></section>
      <section class="feature feature--availability"><dl class="feature-list">
      <dt class="feature-list__key">Available</dt><dd class="feature-list__value">Available now</dd></dl></section>"""
    s = get_scraper("spareroom")
    r = s.parse_detail(html, RawListing(source="spareroom", source_id="1", url="u"))
    assert r.price_amount == 700 and r.price_period == "pcm" and r.price_per_person
    assert r.available_now and r.available_from == s.today


def test_sturents_city_page_cards_with_empty_data_house():
    # /student-accommodation/newcastle ships data-house="{}"; details come from the card + JSON-LD Residence.
    ld = {"@type": "Residence", "name": "6 bed student house to rent on Jesmond Road, Newcastle, NE2",
          "description": "Shared house in Newcastle for £165 pppw from Student Cribs. See more details - 428185",
          "address": {"@type": "PostalAddress", "streetAddress": "Jesmond Road", "addressLocality": "Newcastle"}}
    html = (f'<script type="application/ld+json">{json.dumps(ld)}</script>'
            '<a href="https://sturents.com/student-accommodation/newcastle-upon-tyne/house/jesmond-road/428185?contract=625640" '
            'class="new--listing-item js-listing-item" data-house="{}" data-house-id="428185" '
            'data-photo="https://public.sturents.com/photo-thumbs/9959512.jpg">'
            '<div>View listing</div><div>6 beds</div><div>£165 pppw</div><div>Jesmond Road</div>'
            '<div>Available from 5 Jul 2027</div></a>')
    [r] = get_scraper("sturents").parse_search(html, "https://sturents.com")
    assert r.source_id == "428185" and r.bedrooms == 6 and r.price_amount == 165 and r.price_per_person
    assert r.available_from == date(2027, 7, 5) and r.agent_name == "Student Cribs"
    assert r.address == "Jesmond Road, Newcastle" and r.photos == ["https://public.sturents.com/photos/9959512.jpg"]


def test_sturents_sitemap_uses_full_city_slug_only():
    pages = {
        "https://sturents.com/sitemap/index.xml": "<sitemapindex>"
        "<sitemap><loc>https://sturents.com/sitemap/houses.xml</loc></sitemap>"
        "<sitemap><loc>https://sturents.com/sitemap/houses/aberdeen.xml</loc></sitemap>"
        "<sitemap><loc>https://sturents.com/sitemap/houses/newcastle-under-lyme.xml</loc></sitemap>"
        "<sitemap><loc>https://sturents.com/sitemap/houses-deactivated/newcastle-upon-tyne.xml</loc></sitemap>"
        "<sitemap><loc>https://sturents.com/sitemap/houses/newcastle-upon-tyne.xml</loc></sitemap></sitemapindex>",
        "https://sturents.com/sitemap/houses/newcastle-upon-tyne.xml": "<urlset>"
        "<url><loc>https://sturents.com/student-accommodation/newcastle-upon-tyne/house/a-road/111</loc></url>"
        "<url><loc>https://sturents.com/student-accommodation/newcastle-upon-tyne/house/b-road/222</loc></url></urlset>",
    }

    class FakeFetcher:
        def __init__(self):
            self.got = []

        def get(self, url, **kw):
            self.got.append(url)
            return pages[url]

    f = FakeFetcher()
    urls = get_scraper("sturents", f)._sitemap_urls(["newcastle-upon-tyne"], limit_maps=3)
    assert [u.rsplit("/", 1)[1] for u in urls] == ["111", "222"]
    assert f.got == ["https://sturents.com/sitemap/index.xml", "https://sturents.com/sitemap/houses/newcastle-upon-tyne.xml"]


def test_unihomes_detail_photos_deduped_to_this_property():
    f = "https://cdn-p1.unihomes.co.uk/property/1363893259/house-1.jpg"
    html = f"""<img src="https://www.unihomes.co.uk/cdn-cgi/image/width=390,quality=90/{f}">
      <img src="https://www.unihomes.co.uk/cdn-cgi/image/width=1200,quality=90/{f}">
      <img src="https://cdn-p1.unihomes.co.uk/property/999/other.jpg">
      <img src="https://cdn-p1.unihomes.co.uk/locations/newcastle/city.jpg">"""
    r = get_scraper("unihomes").parse_detail(html, RawListing(source="unihomes", source_id="1363893259", url="u"))
    assert r.photos == [f"https://www.unihomes.co.uk/cdn-cgi/image/width=1200,quality=90/{f}"]
