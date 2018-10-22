var echarts_electricity_graph = echarts.init(document.getElementById('echarts-electricity-graph'));


$(document).ready(function(){
	/* Responsiveness. */
	$(window).resize(function() {
		echarts_electricity_graph.resize();
	});
});
	

function render_electricity_graph(xhr_data)
{
    var echarts_options = {
	    title: {
	        text: text_electricity_header,
	        left: 'center'
	    },
        color: [
        	electricity_delivered_color,
        	electricity_delivered_alternate_color,
        ],
    	tooltip : {
            trigger: 'axis',
            axisPointer: {
                type: 'shadow',
                label: {
                    show: true
                }
            }
        },
        calculable : true,
        grid: {
            top: '12%',
            left: '1%',
            right: '1%',
            containLabel: true
        },
        xAxis: [
            {
                type : 'category',
                data : xhr_data.x
            }
        ],
        yAxis: [
            {
                type : 'value'
            }
        ],
        series : null
    };
    
    if (xhr_data.electricity1 && xhr_data.electricity2)
	{
    	echarts_options.series = [
            {
            	smooth: true,
                name: text_electricity1_delivered,
                type: 'line',
                areaStyle: {},
                data: xhr_data.electricity1
            },
            {
            	smooth: true,
                name: text_electricity2_delivered,
                type: 'line',
                areaStyle: {},
                data: xhr_data.electricity2
            }
        ]
	}
    else if (xhr_data.electricity_merged)
	{
    	echarts_options.series = [
            {
            	smooth: true,
                name: text_electricity_merged_delivered,
                type: 'line',
                areaStyle: {},
                data: xhr_data.electricity_merged
            }
        ]
	}
	
	echarts_electricity_graph.setOption(echarts_options);
}
