"""并发删除/恢复/查询：任一时刻只观察到单一一致状态。"""
import threading

from app.errors import DomainError


def test_concurrent_delete_and_restore_never_mixed_state(service, alice):
    r = service.create("a", "alice")
    errors = []

    def deleter():
        for _ in range(50):
            try:
                service.delete(r.id, alice)
            except DomainError:
                pass
            except Exception as e:  # 不允许出现领域错误以外的异常
                errors.append(e)

    def restorer():
        for _ in range(50):
            try:
                service.restore(r.id, alice)
            except DomainError:
                pass
            except Exception as e:
                errors.append(e)

    def reader():
        for _ in range(200):
            try:
                v = service.get(r.id, alice, include_deleted=True)
                # 状态必须自洽：deleted 与 deleted_at 同生同灭，无字段级混合
                assert v.deleted == (v.deleted_at is not None)
                assert v.deleted == (v.deleted_by is not None)
            except DomainError:
                pass

    threads = [threading.Thread(target=deleter),
               threading.Thread(target=restorer),
               threading.Thread(target=reader),
               threading.Thread(target=reader)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    final = service.get(r.id, alice, include_deleted=True)
    assert final.deleted == (final.deleted_at is not None)


def test_concurrent_same_name_create_only_one_wins(service, alice):
    r = service.create("a", "alice")
    service.delete(r.id, alice)  # 名称已释放
    winners, losers = [], []

    def creator():
        try:
            winners.append(service.create("a", "alice").id)
        except DomainError as e:
            losers.append(e.status_code)

    threads = [threading.Thread(target=creator) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(winners) == 1          # 恰好一个创建成功
    assert losers == [409] * 9        # 其余全部确定性冲突
    assert service.count(alice) == 1  # 无重复数据
