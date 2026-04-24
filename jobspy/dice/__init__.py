from __future__ import annotations

import re
import time
from datetime import datetime

from jobspy.model import (
    Compensation,
    CompensationInterval,
    JobPost,
    JobResponse,
    JobType,
    Location,
    Scraper,
    ScraperInput,
    Site,
)
from jobspy.util import (
    create_logger,
    create_session,
)

log = create_logger("Dice")


class DiceScraper(Scraper):
    base_url = "https://www.dice.com"
    search_url = "https://www.dice.com/jobs"

    def __init__(
        self, proxies: list[str] | str | None = None, ca_cert: str | None = None, user_agent: str | None = None
    ):
        super().__init__(Site.DICE, proxies=proxies, ca_cert=ca_cert, user_agent=user_agent)
        self.session = create_session(proxies=proxies, ca_cert=ca_cert, is_tls=True)
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        })
        self.scraper_input = None
        self.seen_urls = set()
        self.jobs_per_page = 20

    def scrape(self, scraper_input: ScraperInput) -> JobResponse:
        self.scraper_input = scraper_input
        job_list = []
        page = 1

        while len(job_list) < scraper_input.results_wanted + scraper_input.offset:
            log.info(f"search page: {page}")
            jobs = self._scrape_page(page)
            if not jobs:
                log.info(f"found no jobs on page: {page}")
                break
            job_list.extend(jobs)
            page += 1
            time.sleep(2)

        return JobResponse(
            jobs=job_list[
                scraper_input.offset : scraper_input.offset + scraper_input.results_wanted
            ]
        )

    def _scrape_page(self, page: int) -> list[JobPost]:
        jobs = []
        params = self._build_params(page)

        try:
            response = self.session.get(self.search_url, params=params)
        except Exception as e:
            log.error(f"Request failed: {e}")
            return jobs

        if not response.ok:
            log.error(f"Response status: {response.status_code}")
            return jobs

        jobs = self._parse_jobs(response.text)
        return jobs

    def _build_params(self, page: int) -> dict:
        params = {
            "q": self.scraper_input.search_term or "",
            "location": self.scraper_input.location or "",
            "page": page,
            "pageSize": self.jobs_per_page,
        }

        if self.scraper_input.distance:
            params["radius"] = self.scraper_input.distance
            params["radiusUnit"] = "mi"

        if self.scraper_input.job_type:
            job_type_map = {
                JobType.FULL_TIME: "FULLTIME",
                JobType.PART_TIME: "PARTTIME",
                JobType.CONTRACT: "CONTRACTS",
                JobType.INTERNSHIP: "INTERNSHIP",
            }
            if emp_type := job_type_map.get(self.scraper_input.job_type):
                params["employmentTypes"] = emp_type

        if self.scraper_input.is_remote:
            params["workFromHome"] = "true"

        if self.scraper_input.hours_old:
            if self.scraper_input.hours_old <= 24:
                params["postedDate"] = "Today"
            elif self.scraper_input.hours_old <= 72:
                params["postedDate"] = "Last 3 Days"
            else:
                params["postedDate"] = "Last 7 Days"

        return params

    def _parse_jobs(self, html: str) -> list[JobPost]:
        jobs = []

        detail_aria_pattern = r'data-testid="job-search-job-detail-link"[^>]*aria-label="([^"]+)"'
        titles = re.findall(detail_aria_pattern, html)
        
        url_pattern = r'href="(https://www\.dice\.com/job-detail/[0-9a-f-]+)"'
        urls = re.findall(url_pattern, html)
        
        seen_ids = set()
        for i, url_match in enumerate(urls):
            uuid = url_match.split("/")[-1]
            short_id = uuid[:8]

            if short_id in seen_ids:
                continue
            seen_ids.add(short_id)

            job_url = url_match
            if job_url in self.seen_urls:
                continue
            self.seen_urls.add(job_url)

            title = titles[i] if i < len(titles) else ""
            
            if title and title[0].isdigit():
                first_letter_index = 0
                for j, c in enumerate(title):
                    if c.isalpha():
                        first_letter_index = j
                        break
                title = title[first_letter_index:]

            job_data = self._extract_job_data(html, uuid, title, job_url)

            if job_data:
                jobs.append(job_data)

        return jobs

    def _extract_job_data(self, html: str, uuid: str, title: str, job_url: str) -> JobPost | None:
        url_pattern = 'href="' + job_url + '"'
        match = re.search(url_pattern, html)
        if not match:
            return None

        start_pos = match.start()
        context_start = max(0, start_pos - 2000)
        context_end = min(len(html), start_pos + 2000)
        context = html[context_start:context_end]

        company_name = None
        company_match = re.search(r'companyname=([^"&]+)', context)
        if company_match:
            company_name = company_match.group(1).replace("%20", " ")

        city, state = self._parse_location(context)
        date_posted = self._parse_date(context)
        
        # If no location in list, fetch detail page for more data
        if (not city or not state) and self.scraper_input.location and "remote" not in self.scraper_input.location.lower():
            try:
                detail_html = self.session.get(job_url).text
                city, state = self._parse_detail_location(detail_html)
            except:
                pass
        job_type = self._parse_job_type(context)
        if job_type:
            job_type = [job_type]

        comp = self._parse_compensation(context)

        return JobPost(
            id=f"dice-{uuid[:8]}",
            title=title,
            company_name=company_name,
            location=Location(city=city, state=state, country="USA"),
            job_type=job_type,
            compensation=comp,
            date_posted=date_posted,
            job_url=job_url,
        )

    def _parse_location(self, context: str) -> tuple[str | None, str | None]:
        # Try pattern like "San Jose, California•" or "San Jose, CA•"
        patterns = [
            r'">([A-Z][a-z]+(?: [A-Z][a-z]+)*),\s*([A-Z][a-z]{2,})(?:•|<)',
            r'">([A-Za-z\s]+),\s*([A-Z]{2})\s*•',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, context)
            if match:
                city = match.group(1).strip()
                state = match.group(2).strip()
                # Validate state is 2 letters or a known state name
                if len(state) == 2 or state in ["California", "Texas", "Florida", "Georgia", "Virginia", "Washington"]:
                    return city, state
        
        return None, None

    def _parse_date(self, context: str) -> str | None:
        date_match = re.search(r'•(Today|\d+\s*days?\s*ago)', context)
        if not date_match:
            return None
            
        date_text = date_match.group(1)
        if date_text == "Today":
            return datetime.now().strftime("%Y-%m-%d")
        
        days_match = re.search(r'(\d+)', date_text)
        if days_match:
            days = int(days_match.group(1))
            date = datetime.now() - datetime.timedelta(days=days)
            return date.strftime("%Y-%m-%d")
        
        return None

    def _parse_job_type(self, context: str) -> JobType | None:
        text_lower = context.lower()
        if "full time" in text_lower or "full-time" in text_lower:
            return JobType.FULL_TIME
        if "part time" in text_lower or "part-time" in text_lower:
            return JobType.PART_TIME
        if "contract" in text_lower:
            return JobType.CONTRACT
        if "intern" in text_lower:
            return JobType.INTERNSHIP
        return None

    def _parse_compensation(self, context: str) -> Compensation | None:
        salaries = re.findall(r'\$[\d,]+(?:\s*-\s*\$[\d,]+)?(?:\s*/(?:hr|year))?', context)
        if not salaries:
            return None

        salary_str = salaries[0]
        numbers = re.findall(r'\$?([\d,]+)', salary_str)
        if not numbers:
            return None

        amounts = [int(n.replace(",", "")) for n in numbers]
        min_amount = min(amounts)
        max_amount = max(amounts) if len(amounts) > 1 else min_amount

        interval_str = "hourly" if "/hr" in salary_str.lower() else "yearly"
        interval = CompensationInterval.HOURLY if interval_str == "hourly" else CompensationInterval.YEARLY

        return Compensation(
            interval=interval,
            min_amount=float(min_amount),
            max_amount=float(max_amount),
            currency="USD",
        )

    def _parse_detail_location(self, html: str) -> tuple[str | None, str | None]:
        import json
        import re
        
        # Find JSON-LD schema with simpler regex (capture all content between script tags)
        json_match = re.search(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
        if json_match:
            content = json_match.group(1)
            try:
                data = json.loads(content)
                job_loc = data.get('jobLocation', {})
                if isinstance(job_loc, dict):
                    addr = job_loc.get('address', {})
                    if isinstance(addr, dict):
                        city = addr.get('addressLocality')
                        region = addr.get('addressRegion')
                        if city:
                            return city, region
            except:
                pass
        
        # Fallback: parse from description text like "City: San Jose" and "State/Province: CA"
        city_match = re.search(r'City:\s*([^<\n]+)', html)
        state_match = re.search(r'State/Province:\s*([A-Z]{2})', html)
        
        if city_match:
            city = city_match.group(1).strip()
            state = state_match.group(1) if state_match else None
            return city, state
        
        return None, None