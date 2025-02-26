# Copyright (C) 2017 Semester.ly Technologies, LLC
#
# Semester.ly is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Semester.ly is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

import itertools
from collections import namedtuple

from courses.utils import get_sections_by_section_type
from timetable.models import Section, Semester
from timetable.school_mappers import SCHOOLS_MAP
from student.models import PersonalTimetable
from parsing.library.utils import short_date


MAX_RETURN = 60  # Max number of timetables we want to consider

Slot = namedtuple("Slot", "course section offerings is_locked")
Timetable = namedtuple("Timetable", "courses sections has_conflict")


class DisplayTimetable:
    """Object that represents the frontend's interpretation of a timetable."""

    def __init__(
        self, slots, has_conflict, show_weekend, name="", events=None, id=None
    ):
        self.slots = slots
        self.has_conflict = has_conflict
        self.name = name
        self.show_weekend = show_weekend
        self.events = events or []
        self.id = id

    @classmethod
    def from_model(cls, timetable):
        """Create DisplayTimetable from Timetable instance."""
        slots = [
            Slot(
                section.course,
                section,
                section.offering_set.all(),
                is_locked=True,
            )
            for section in timetable.sections.all()
        ]
        id = timetable.id if isinstance(timetable, PersonalTimetable) else None
        # set show_weekend to False if timetable is not a PersonalTimetable (ex: SharedTimetable)
        show_weekend = (
            timetable.show_weekend
            if isinstance(timetable, PersonalTimetable)
            else False
        )
        return DisplayTimetable(
            slots,
            timetable.has_conflict,
            show_weekend,
            getattr(timetable, "name", ""),
            getattr(timetable, "events", []),
            id,
        )


def courses_to_timetables(
    courses,
    locked_sections,
    semester,
    school,
    custom_events,
    with_conflicts,
    show_weekend,
):
    all_offerings = courses_to_slots(courses, locked_sections, semester)
    timetable_gen = slots_to_timetables(
        all_offerings, school, custom_events, with_conflicts, show_weekend
    )
    return itertools.islice(timetable_gen, MAX_RETURN)


def courses_to_slots(courses, locked_sections, semester):
    """
    Return a list of lists of Slots. Each Slot sublist represents the list of
    possibilities for a given course and section type, i.e. a valid timetable consists
    of any one slot from each sublist.
    """
    slots = []
    for course in courses:
        grouped = get_sections_by_section_type(course, semester)
        for section_type, sections in grouped.items():
            locked_section_code = locked_sections.get(str(course.id), {}).get(
                section_type
            )
            section_codes = [section.meeting_section for section in sections]
            if locked_section_code in section_codes:
                locked_section = next(
                    s for s in sections if s.meeting_section == locked_section_code
                )
                locked_slot = Slot(
                    course,
                    locked_section,
                    locked_section.offering_set.all(),
                    is_locked=True,
                )
                slots.append([locked_slot])
            else:
                possibilities = [
                    Slot(
                        course,
                        section,
                        section.offering_set.all(),
                        is_locked=False,
                    )
                    for section in sections
                ]
                slots.append(possibilities)
    return slots


def slots_to_timetables(slots, school, custom_events, with_conflicts, show_weekend):
    """Generate timetables in a depth-first manner based on a list of slots."""
    num_offerings, num_permutations_remaining = get_xproduct_indicies(slots)
    total_num_permutations = num_permutations_remaining.pop(0)
    for p in range(total_num_permutations):  # for each possible tt
        current_tt = []
        day_to_usage = get_day_to_usage(custom_events, school)
        num_conflicts = 0
        add_tt = True
        for i in range(len(slots)):  # add an offering for the next section
            j = int((p / num_permutations_remaining[i]) % num_offerings[i])
            num_added_conflicts = add_meeting_and_check_conflict(
                day_to_usage, slots[i][j], school
            )
            num_conflicts += num_added_conflicts
            if num_conflicts and not with_conflicts:
                add_tt = False
                break
            current_tt.append(slots[i][j])
        if add_tt and current_tt:
            has_conflict = bool(num_conflicts)
            current_tt = DisplayTimetable(current_tt, has_conflict, show_weekend)
            yield current_tt


def update_locked_sections(locked_sections, cid, locked_section, semester):
    """
    Take cid of new course, and locked section for that course
    and toggle its locked status (ie if was locked, unlock and vice versa.
    """
    section_type = Section.objects.filter(
        semester=semester, course=cid, meeting_section=locked_section
    )[0].section_type
    if locked_sections[cid].get(section_type, "") == locked_section:  # already locked
        locked_sections[cid][section_type] = ""  # unlock that section_type
    else:  # add as locked section for that section_type
        locked_sections[cid][section_type] = locked_section


def get_xproduct_indicies(lists):
    """
    Takes a list of lists and returns two lists of indicies needed to iterate
    through the cross product of the input.
    """
    num_offerings = []
    num_permutations_remaining = [1]
    for i in range(len(lists) - 1, -1, -1):
        length = len(lists[i])
        num_offerings.insert(0, length)
        num_permutations_remaining.insert(0, length * num_permutations_remaining[0])
    return num_offerings, num_permutations_remaining


def add_meeting_and_check_conflict(day_to_usage, new_meeting, school):
    """
    Takes a @day_to_usage dictionary and a @new_meeting section and
    returns a tuple of the updated day_to_usage dict and a boolean
    which is True if conflict, False otherwise.
    """
    course_offerings = new_meeting[2]
    new_conflicts = 0

    for offering in course_offerings:
        day = offering.day
        if day != "U":
            for slot in find_slots_to_fill(
                offering.time_start, offering.time_end, school
            ):
                previous_len = max(1, len(day_to_usage[day][slot]))
                potential_conflict_found = False
                if hasattr(offering, "has_potential_conflict"):
                    potential_conflict_found = offering.has_potential_conflict
                else:
                    for existing_offering in day_to_usage[day][slot]:
                        if existing_offering == "custom_slot":
                            break
                        potential_conflict_found = can_potentially_conflict(
                            existing_offering.date_start,
                            existing_offering.date_end,
                            offering.date_start,
                            offering.date_end,
                        )
                        offering.has_potential_conflict = potential_conflict_found
                        break
                day_to_usage[day][slot].add(offering)
                if potential_conflict_found:
                    new_conflicts += len(day_to_usage[day][slot]) - previous_len
    return new_conflicts


def can_potentially_conflict(
    course_1_date_start, course_1_date_end, course_2_date_start, course_2_date_end
):
    """Checks two courses start & end dates to see whether they can overlap and
    hence potentially conflict. If any of the values are passed as None it will
    automatically consider that they can potentially conflict. Input type is
    string but has to be in a reasonable date format.

    Arguments:
        course_1_date_start {[string]} -- [course 1 start date in a reasonable date format]
        course_1_date_end {[string]} -- [course 1 end date in a reasonable date format]
        course_2_date_start {[string]} -- [course 2 start date in a reasonable date format]
        course_2_date_end {[string]} -- [course 2 end date in a reasonable date format]

    Returns:
        [bool] -- [True if if dates ranges of course 1 and 2 overlap, otherwise False]
    """
    potential_conflict_found = False
    course_1_date_start = short_date(course_1_date_start)
    course_1_date_end = short_date(course_1_date_end)
    course_2_date_start = short_date(course_2_date_start)
    course_2_date_end = short_date(course_2_date_end)
    if (
        course_1_date_start is None
        or course_1_date_end is None
        or course_2_date_start is None
        or course_2_date_end is None
    ):
        return True
    else:
        return (
            course_2_date_start <= course_1_date_end
            and course_2_date_end >= course_1_date_start
        )


def find_slots_to_fill(start, end, school):
    """
    Take a @start and @end time in the format found in the coursefinder (e.g. 9:00, 16:30),
    and return the indices of the slots in the array which represents times from 8:00am
    to 10pm that would be filled by the given @start and @end. For example, for uoft
    input: '10:30', '13:00'
    output: [5, 6, 7, 8, 9]
    """
    start_hour, start_minute = get_hours_minutes(start)
    end_hour, end_minute = get_hours_minutes(end)

    return list(
        range(
            int(get_time_index(start_hour, start_minute, school)),
            int(get_time_index(end_hour, end_minute, school)),
        )
    )


def get_time_index(hours, minutes, school):
    """Take number of hours and minutes, and return the corresponding time slot index"""
    return (
        hours * (60 / SCHOOLS_MAP[school].granularity)
        + minutes / SCHOOLS_MAP[school].granularity
    )


def get_hours_minutes(time_string):
    """
    Return tuple of two integers representing the hour and the time
    given a string representation of time.
    e.g. '14:20' -> (14, 20)
    """
    return (
        get_hour_from_string_time(time_string),
        get_minute_from_string_time(time_string),
    )


def get_hour_from_string_time(time_string):
    """Get hour as an int from time as a string."""
    return (
        int(time_string[: time_string.index(":")])
        if ":" in time_string
        else int(time_string)
    )


def get_minute_from_string_time(time_string):
    """Get minute as an int from time as a string."""
    return int(time_string[time_string.index(":") + 1 :] if ":" in time_string else 0)


def get_day_to_usage(custom_events, school):
    """Initialize day_to_usage dictionary, which has custom events blocked out."""
    day_to_usage = {
        day: [set() for _ in range(int(24 * 60 / SCHOOLS_MAP[school].granularity))]
        for day in ["M", "T", "W", "R", "F", "S", "U"]
    }

    for event in custom_events:
        for slot in find_slots_to_fill(event["time_start"], event["time_end"], school):
            day_to_usage[event["day"]][slot].add("custom_slot")

    return day_to_usage


def get_current_semesters(school):
    """List of semesters ordered by academic temporality.

    For a given school, get the possible semesters ordered by the most recent
    year for each semester that has course data, and return a list of
    (semester name, year) pairs.
    """
    semesters = []
    for year, terms in reversed(list(SCHOOLS_MAP[school].active_semesters.items())):
        for term in terms:
            # Ensure DB has all semesters.
            Semester.objects.update_or_create(name=term, year=year)

            semesters.append({"name": term, "year": str(year)})

    return semesters
