from myapp.passwords import hash_password, verify_password


def test_hash_and_verify():
    stored = hash_password("correct horse")
    assert stored.startswith("pbkdf2_sha256$600000$")
    assert verify_password("correct horse", stored)
    assert not verify_password("wrong", stored)


def test_same_password_gets_different_salt():
    assert hash_password("same") != hash_password("same")


def test_malformed_hash_is_rejected():
    assert not verify_password("x", "CHANGE-ME-staff-0000")   # 舊的明碼
    assert not verify_password("x", "md5$1$abc$def")          # 不認得的演算法
    assert not verify_password("x", b"pbkdf2_sha256$1$!!!$???")
