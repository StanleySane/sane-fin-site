import typing

from django.core.paginator import Paginator


class Pagination:
    result_list: typing.Iterable
    can_show_all: bool
    show_all: bool
    multi_page: bool
    paginator: Paginator
    page_num: int
    list_per_page = 20
    list_max_show_all = 2000

    def __init__(self, object_list: typing.Iterable, page_num: int, show_all: bool):
        paginator = Paginator(object_list, self.list_per_page)
        result_count = paginator.count
        can_show_all = result_count <= self.list_max_show_all
        multi_page = result_count > self.list_per_page

        pagination_required = (not show_all or not can_show_all) and multi_page
        page_range = (paginator.get_elided_page_range(page_num, on_each_side=2, on_ends=2)
                      if pagination_required
                      else [])
        need_show_all_link = can_show_all and not show_all and multi_page

        if (show_all and can_show_all) or not multi_page:
            result_list = object_list
        else:
            result_list = paginator.get_page(page_num).object_list

        self.result_list = result_list
        self.can_show_all = can_show_all
        self.show_all = show_all
        self.multi_page = multi_page
        self.paginator = paginator
        self.page_num = page_num
        self.pagination_required = pagination_required
        self.page_range = page_range
        self.need_show_all_link = need_show_all_link
