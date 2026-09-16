"""Judge failures must not be silently recorded as successful assessments."""
import pytest
from scripts.judge_spider_answers import assess, validate_judgment


@pytest.mark.parametrize('bad', [True, 2, '1', -1])
def test_invalid_rubric_dimensions_are_rejected(bad):
    with pytest.raises(ValueError):
        validate_judgment({'correctness':bad,'completeness':1,'groundedness':1,'reason':'x'})


def test_failed_agent_is_not_sent_to_judge():
    class NoNetwork:
        def post(self,*args,**kwargs):
            raise AssertionError('must not call API')
    result=assess({'task':{'status':'failed'}},NoNetwork(),'judge')
    assert not result['judged']
    assert result['failure']=='agent_no_successful_answer'


def test_judge_service_error_is_not_a_positive_score():
    class Response:
        status_code=429
    class Client:
        def post(self,*args,**kwargs): return Response()
    item={'question':'How many?', 'reference_sql':'select 1','reference_rows':[[1]],
          'task':{'status':'success','analysis':'1','queries':[]}}
    result=assess(item,Client(),'judge')
    assert result=={'judged':False,'failure':'judge_http_429'}
